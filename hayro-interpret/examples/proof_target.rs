//! Read the six controlled `DeviceN` pages and calculate paper/cyan/orange coverage.

use hayro_interpret::Function;
use hayro_interpret::color::{ColorSpace, DeviceNAttributes};
use hayro_syntax::Pdf;
use hayro_syntax::content::ops::TypedInstruction;
use hayro_syntax::object::{Array, Dict, Name, Object};
use hayro_syntax::page::Page;
use std::io::{self, Read};

fn dot_gain(source: Object<'_>, tint: f32) -> Option<f32> {
    let Object::Dict(dict) = &source else {
        return None;
    };
    if dict.data().len() > 256
        || dict.get::<u8>(b"FunctionType") != Some(2)
        || dict.get::<[f32; 2]>(b"Domain") != Some([0.0, 1.0])
        || dict.get::<[f32; 1]>(b"C0") != Some([0.0])
        || dict.get::<[f32; 1]>(b"C1") != Some([1.0])
        || !dict
            .get::<f32>(b"N")
            .is_some_and(|n| n.is_finite() && n > 0.0)
    {
        return None;
    }
    let mut value = [0.0];
    Function::new(&source)?.eval(&[tint], &mut value)?;
    (0.0..=1.0).contains(&value[0]).then_some(value[0])
}

fn analyze(page: &Page<'_>) -> Option<String> {
    let ink = page
        .resources()
        .get_color_space(&Name::new_unescaped(b"Ink"))?;
    let Object::Array(ref source) = ink else {
        return None;
    };
    let mut color_space = source.flex_iter();
    color_space.next::<Name<'_>>()?;
    let names = color_space.next::<Array<'_>>()?;
    let mut names = names.iter::<Name<'_>>();
    let first = names.next()?;
    let second = names.next()?;
    let spot = second.as_ref();
    if first.as_ref() != b"Cyan" || !matches!(spot, b"Orange" | b"White") || names.next().is_some()
    {
        return None;
    }
    let attrs = DeviceNAttributes::from_object(&ink)?;
    let hints = attrs.mixing_hints()?;
    let mut operations = page.typed_operations();
    let mut tints = None;
    while let Some(op) = operations.next() {
        if let TypedInstruction::NonStrokeColorNamed(values) = op {
            if tints.is_some() || values.1.is_some() || values.0.len() != 2 {
                return None;
            }
            tints = Some([values.0[0].as_f32(), values.0[1].as_f32()]);
        }
    }
    let tints = tints?;
    if !tints.iter().all(|t| (0.0..=1.0).contains(t)) {
        return None;
    }
    let order = hints.printing_order()?;
    let mut order = order.iter::<Name<'_>>();
    let first = order.next()?;
    let second = order.next()?;
    if order.next().is_some() {
        return None;
    }
    let (first, second) = (first.as_ref(), second.as_ref());
    if first == second
        || ![first, second].contains(&b"Cyan".as_slice())
        || ![first, second].contains(&spot)
    {
        return None;
    }

    // The calibrated full-spot surface is specified by its separation tint transform.
    let colorant = attrs.colorant(spot)?;
    let mut sep = colorant.flex_iter();
    if sep.next::<Name<'_>>()?.as_ref() != b"Separation" || sep.next::<Name<'_>>()?.as_ref() != spot
    {
        return None;
    }
    sep.next::<Array<'_>>()?;
    let endpoint = sep.next::<Dict<'_>>()?.get::<[f32; 4]>(b"C1")?;
    let spot_slot = match endpoint {
        [0.0, 0.0, 0.0, 0.0] => 0,
        [0.0, 0.65, 1.0, 0.0] => 2,
        _ => return None,
    };
    let mut portions = [1.0, 0.0, 0.0];
    let spot_solidity = hints.solidity(spot)?;
    for ink in [first, second] {
        let process = ink == b"Cyan";
        let coverage = dot_gain(hints.dot_gain(ink)?, tints[usize::from(!process)])?;
        let solidity = hints.solidity(ink)?;
        let slot = if process { 1 } else { spot_slot };
        if slot == 0 && solidity == 0.0 {
            continue;
        }
        if solidity != 1.0 {
            return None;
        }
        for part in &mut portions {
            *part *= 1.0 - coverage;
        }
        portions[slot] += coverage;
    }
    let alternate = ColorSpace::from_object(ink)?;
    if alternate.to_rgba(&tints, 1.0).to_rgba8() != [191, 127, 255, 255]
        || alternate.to_rgba(&[0.25, 0.75], 1.0).to_rgba8() != [191, 64, 255, 255]
    {
        return None;
    }
    Some(format!(
        "{}\t{spot_solidity}\t{},{}\t{}\t{}\t{}",
        std::str::from_utf8(spot).ok()?,
        std::str::from_utf8(first).ok()?,
        std::str::from_utf8(second).ok()?,
        portions[0],
        portions[1],
        portions[2]
    ))
}

fn main() -> io::Result<()> {
    let path = std::env::args_os()
        .nth(1)
        .ok_or(io::ErrorKind::InvalidInput)?;
    let mut data = Vec::new();
    std::fs::File::open(path)?
        .take(65537)
        .read_to_end(&mut data)?;
    if data.len() > 65536 {
        return Err(io::ErrorKind::InvalidData.into());
    }
    let pdf = Pdf::new(data).map_err(|_| io::ErrorKind::InvalidData)?;
    if pdf.pages().len() > 8 {
        return Err(io::ErrorKind::InvalidData.into());
    }
    for page in pdf.pages().iter() {
        match analyze(page) {
            Some(row) => println!("{row}"),
            None => println!("REFUSED"),
        }
    }
    Ok(())
}
