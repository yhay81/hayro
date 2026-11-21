"""Generate and inspect a two-ink proofing target under explicitly chosen optical conditions."""

import argparse
import collections
import hashlib
import subprocess
from pathlib import Path

import pikepdf
from PIL import Image

Array, Dict, Name = pikepdf.Array, pikepdf.Dictionary, pikepdf.Name
PROFILE_SHA = "73e1ba37d2bad5bab2a964f40a9eed96209666efc067c3322626214bbef234a0"
CASES = [
    ("Orange", 1, ("Cyan", "Orange")),
    ("Orange", 1, ("Orange", "Cyan")),
    ("White", 1, ("Cyan", "White")),
    ("White", 1, ("White", "Cyan")),
    ("White", 0, ("Cyan", "White")),
    ("White", 0, ("White", "Cyan")),
]
PAPER = (0, 0, 0, 0)
CYAN = (1, 0, 0, 0)
ORANGE = (0, 0.65, 1, 0)
SWATCHES = (PAPER, CYAN, ORANGE)


def function(exponent, start=(0,), end=(1,)):
    return Dict(
        FunctionType=2, Domain=Array([0, 1]), C0=Array(start), C1=Array(end), N=exponent
    )


def profile_space(pdf, profile):
    icc = pdf.make_stream(profile)
    icc.N = 4
    icc.Alternate = Name.DeviceCMYK
    return Array([Name.ICCBased, icc])


def new_page(pdf, resources, drawing):
    page = pdf.add_blank_page(page_size=(32, 32))
    page.Resources = Dict(ColorSpace=Dict(resources))
    page.Contents = pdf.make_stream(drawing.encode())


def source_document(profile, path):
    pdf = pikepdf.Pdf.new()
    cmyk = profile_space(pdf, profile)
    alt = pdf.make_stream(b"{ 1 exch sub exch 1 exch sub exch 1 }")
    alt.FunctionType = 4
    alt.Domain = Array([0, 1] * 2)
    alt.Range = Array([0, 1] * 3)
    for spot, solidity, order in CASES:
        separation = Array(
            [
                Name.Separation,
                Name("/" + spot),
                cmyk,
                function(1, PAPER, ORANGE if spot == "Orange" else PAPER),
            ]
        )
        attributes = Dict(
            Subtype=Name.NChannel,
            Process=Dict(
                ColorSpace=cmyk,
                Components=Array([Name.Cyan, Name.Magenta, Name.Yellow, Name.Black]),
            ),
            Colorants=Dict({"/" + spot: separation}),
            MixingHints=Dict(
                Solidities=Dict({"/Default": 1, "/" + spot: solidity}),
                DotGain=Dict({"/Default": function(0.5), "/" + spot: function(2)}),
                PrintingOrder=Array([Name("/" + ink) for ink in order]),
            ),
        )
        space = Array(
            [
                Name.DeviceN,
                Array([Name.Cyan, Name("/" + spot)]),
                Name.DeviceRGB,
                alt,
                attributes,
            ]
        )
        new_page(pdf, {"/Ink": space}, "/Ink cs .25 .5 scn 0 0 32 32 re f")
    pdf.save(path, min_version="1.6", deterministic_id=True)


def physical_reference(profile, path):
    pdf = pikepdf.Pdf.new()
    cmyk = profile_space(pdf, profile)

    def rect(ink, x, y, w, h):
        tint = " ".join(map(str, ink))
        return f"/Print cs {tint} scn {x} {y} {w} {h} re f\n"

    # Independent orthogonal screens. Cyan covers half the paper; the spot covers one quarter.
    background = rect(PAPER, 0, 0, 32, 32)
    c = rect(CYAN, 0, 0, 16, 32)
    o = rect(ORANGE, 0, 0, 32, 8)
    w = rect(PAPER, 0, 0, 32, 8)
    for marks in (c + o, o + c, c + w, w + c, c, c):
        new_page(pdf, {"/Print": cmyk}, background + marks)
    # Device-generated solid controls distinguish the press profile from display RGB.
    for ink in SWATCHES:
        new_page(pdf, {"/Print": cmyk}, rect(ink, 0, 0, 32, 32))
    pdf.save(path, min_version="1.6", deterministic_id=True)


def negative_controls(source, path):
    with pikepdf.Pdf.open(source) as pdf:
        for i, page in enumerate(pdf.pages):
            attrs = page.Resources.ColorSpace.Ink[4]
            hints = attrs.MixingHints
            if i == 0:
                hints.Solidities.Orange = 2
            elif i == 1:
                hints.PrintingOrder = Array([Name.Cyan, Name.Cyan])
            elif i == 2:
                # The calibrated proofing device only accepts the bounded exponential functions.
                hints.DotGain.White = page.Resources.ColorSpace.Ink[3]
            elif i == 3:
                hints.DotGain.White.Marker = "x" * 300
            elif i == 4:
                del hints.DotGain.Default
            else:
                hints.Solidities.White = 0.5
        pdf.save(path, min_version="1.6", deterministic_id=True)


def solid_xyz(path):
    # Use the same embedded profile for relative-colorimetric D50 PCS solid endpoints.
    swatches = "".join(" ".join(str(100 * x) for x in row) + "\n" for row in SWATCHES)
    result = subprocess.run(
        ["transicc", "-i" + str(path), "-o*XYZ", "-t1", "-n"],
        input=swatches,
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    )
    return [
        tuple(map(float, line.split()))
        for line in result.stdout.splitlines()
        if line.strip()
    ]


def screen_counts(prefix):
    rasters = []
    for page in range(1, 10):
        with Image.open(prefix.format(page)) as image:
            assert image.size == (32, 32)
            pixels = image.convert("RGB")
            rasters.append(
                collections.Counter(
                    pixels.getpixel((x, y)) for x in range(32) for y in range(32)
                )
            )
    colors = []
    for swatch in rasters[6:]:
        assert len(swatch) == 1
        colors.append(next(iter(swatch)))
    assert len(set(colors)) == 3
    for row in rasters[:6]:
        assert set(row) <= set(colors)
    return [tuple(row[c] for c in colors) for row in rasters[:6]]


def check(directory, profile):
    source = directory / "source.pdf"
    physical = directory / "physical.pdf"
    with pikepdf.Pdf.open(source) as doc:
        for page in doc.pages:
            attrs = page.Resources.ColorSpace.Ink[4]
            embedded = attrs.Process.ColorSpace[1].read_bytes()
            assert embedded == profile.read_bytes()
            for separation in attrs.Colorants.values():
                assert separation[2][1].read_bytes() == embedded
    # A matching output/process profile isolates the literal process tint from ICC conversions.
    subprocess.run(
        [
            "gs",
            "-q",
            "-dBATCH",
            "-dNOPAUSE",
            "-sDEVICE=tiffsep",
            "-r72",
            "-sOutputICCProfile=" + str(profile.resolve()),
            "-sOutputFile=" + str(directory / "plate-%02d.tif"),
            str(source),
        ],
        check=True,
    )
    for page, (spot, _, _) in enumerate(CASES, start=1):
        for ink, expected in [
            ("Cyan", 192),
            (spot, 128),
            ("Magenta", 255),
            ("Yellow", 255),
            ("Black", 255),
        ]:
            with Image.open(directory / f"plate-{page:02}({ink}).tif") as plate:
                assert plate.convert("L").getextrema() == (expected, expected)
    assert (directory / "negative.tsv").read_text().splitlines() == ["REFUSED"] * len(
        CASES
    )
    subprocess.run(
        [
            "gs",
            "-q",
            "-dBATCH",
            "-dNOPAUSE",
            "-sDEVICE=png16m",
            "-r72",
            "-dGraphicsAlphaBits=1",
            "-dTextAlphaBits=1",
            "-dOverrideICC=false",
            "-sOutputFile=" + str(directory / "ref-%02d.png"),
            str(physical),
        ],
        check=True,
    )
    subprocess.run(
        ["pdftoppm", "-r", "72", "-png", str(physical), str(directory / "poppler")],
        check=True,
    )
    gs = screen_counts(str(directory / "ref-{:02}.png"))
    poppler = screen_counts(str(directory / "poppler-{}.png"))
    assert gs == poppler
    anchors = solid_xyz(profile)
    assert len(anchors) == 3
    native = (directory / "native.tsv").read_text().splitlines()
    assert len(native) == len(CASES)
    print("page\tspot\tsolidity\torder\tpaper/cyan/orange pixels\tPCS XYZ 0-100")
    for i, ((spot, solidity, order), row) in enumerate(zip(CASES, native, strict=True)):
        name, level, actual_order, *values = row.split("\t")
        assert (name, int(level), actual_order) == (spot, solidity, ",".join(order))
        predicted = tuple(map(float, values))
        assert len(predicted) == 3 and abs(sum(predicted) - 1) < 1e-8
        counts = gs[i]
        assert tuple(v * 1024 for v in predicted) == counts
        xyz = tuple(
            sum(coverage * pcs[k] for coverage, pcs in zip(predicted, anchors))
            for k in range(3)
        )
        print(
            i + 1,
            spot,
            solidity,
            actual_order,
            "/".join(map(str, counts)),
            ",".join(f"{v:.4f}" for v in xyz),
            sep="\t",
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "check"))
    parser.add_argument("profile", type=Path)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    data = args.profile.read_bytes()
    assert hashlib.sha256(data).hexdigest() == PROFILE_SHA
    args.directory.mkdir(parents=True, exist_ok=True)
    if args.command == "generate":
        source_document(data, args.directory / "source.pdf")
        physical_reference(data, args.directory / "physical.pdf")
        negative_controls(
            args.directory / "source.pdf", args.directory / "negative.pdf"
        )
    else:
        check(args.directory, args.profile)
