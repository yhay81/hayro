use hayro_syntax::object::{Array, Dict, Name, Object};

/// A borrowed view of a `DeviceN` color space's optional ink attributes.
#[derive(Clone, Debug)]
pub struct DeviceNAttributes<'a>(Dict<'a>);

impl<'a> DeviceNAttributes<'a> {
    /// Read the attributes from a `DeviceN` color-space resource.
    pub fn from_object(object: &Object<'a>) -> Option<Self> {
        let Object::Array(array) = object else {
            return None;
        };
        let mut items = array.flex_iter();
        if items.next::<Name<'_>>()?.as_ref() != b"DeviceN" {
            return None;
        }
        items.next_raw()?;
        items.next_raw()?;
        items.next_raw()?;
        Some(Self(items.next::<Dict<'_>>()?))
    }

    /// The source attribute dictionary, including colorants unused by this color space.
    pub fn dictionary(&self) -> &Dict<'a> {
        &self.0
    }

    /// The source color-space array for a named colorant.
    pub fn colorant(&self, name: &[u8]) -> Option<Array<'a>> {
        self.0.get::<Dict<'_>>("Colorants")?.get(name)
    }

    /// The optional mixing hints for a proofing device.
    pub fn mixing_hints(&self) -> Option<DeviceNMixingHints<'a>> {
        self.0
            .get::<Dict<'_>>("MixingHints")
            .map(DeviceNMixingHints)
    }
}

/// The source ink-mixing hints of a `DeviceN` color space.
#[derive(Clone, Debug)]
pub struct DeviceNMixingHints<'a>(Dict<'a>);

impl<'a> DeviceNMixingHints<'a> {
    /// The original printing order, including colorants unused by this color space.
    pub fn printing_order(&self) -> Option<Array<'a>> {
        self.0.get("PrintingOrder")
    }

    /// The named or default ink solidity, or zero when no value is specified.
    pub fn solidity(&self, name: &[u8]) -> Option<f32> {
        if !self.0.contains_key("Solidities") {
            return Some(0.);
        }
        let values = self.0.get::<Dict<'_>>("Solidities")?;
        let key = if values.contains_key(name) {
            name
        } else if values.contains_key("Default") {
            b"Default"
        } else {
            return Some(0.);
        };
        let value = values.get::<f32>(key)?;
        (0. ..=1.).contains(&value).then_some(value)
    }

    /// The named or default dot-gain source object, resolved in the original document.
    pub fn dot_gain(&self, name: &[u8]) -> Option<Object<'a>> {
        let values = self.0.get::<Dict<'_>>("DotGain")?;
        let key = if values.contains_key(name) {
            name
        } else {
            b"Default"
        };
        values.get(key)
    }
}
