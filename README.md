# OTDR Trace Triage

A 144-fiber cable tested at two wavelengths from both ends comes back as 576 OTDR files. Somebody has to open them, compare each splice against the limit, average the two directions, and work out which fibers the crew needs to go back for. I have done that by hand. It takes a day, and the mistakes happen around file three hundred.

This reads the whole batch at once and answers three questions:

- which fibers fail
- why (bad splice, bend, dirty connector, break)
- where to send the crew, as one trip in route order

**Live demo:** https://tochoby.github.io/otdr-trace-triage/ (opens for anyone, no account needed)

The same page [on Claude](https://claude.ai/artifact/U5aaFKn1fBRoYLK9YrEKTQ) adds one AI feature for signed-in viewers: drafting the note to the contractor.

## What it finds

The sample span is made up: 12 fibers, 12.4 km, four splice points, tested at 1310 and 1550 nm from both ends. Six problems are planted in it.

```
OTDR ACCEPTANCE REPORT
CBL-144-01: HH-101 to HUB-A, 12.40 km
12 fibers tested at 1310 and 1550 nm, both directions (48 traces)
Result: 7 pass, 5 fail
================================================================

WHERE TO SEND THE CREW
----------------------------------------------------------------
Connector at HH-101
   Fiber 02  | Clean and reseat the connector and check the jumper. Retest.

S2 at 4.60 km from HH-101
   Fiber 03  | Re-splice.

8.40 km from HH-101
   Fiber 07  | Inspect the cable here for a kink, a tight tie or a crushed duct.

S4 at 9.80 km from HH-101
   Fiber 11  | Locate the break and repair or splice through, then retest both directions.

Connector at HUB-A
   Fiber 09  | Clean and scope the connector; re-terminate if the end face is damaged. Retest.
```

Two of the calls are the ones people get wrong by hand:

- **Fiber 05 passes.** One splice reads as a 0.08 dB gain from one end and a 0.14 dB loss from the other. Judged from either end alone it looks wrong. The two-way average is 0.03 dB, which is a good splice.
- **Fiber 07 is a bend, not a splice.** The loss is 0.46 dB at 1550 nm and 0.05 dB at 1310 nm, at a spot where no other fiber has an event. Sending a splicer would waste the trip. Someone needs to look at the cable.

## Try it

```
python -m triage.cli run examples/span_HH101_HUBA
python -m triage.cli run my_traces/ --splice-max 0.15 --receive 0 --txt report.txt
python -m triage.cli show examples/span_HH101_HUBA/F07_1550_AB.sor
```

No dependencies beyond Python 3.10. In the demo, drop your own `.sor` files on the page; they are read in the browser and not uploaded.

## How it decides

| Check | Default limit | Notes |
|---|---|---|
| Splice loss | 0.10 dB | Average of both directions when both were shot |
| Connector loss | 0.50 dB | Each end |
| Connector reflectance | -45 dB | Worst reading |
| Bend | 1550 worse than 1310 by 0.15 dB | Reported as a bend, with a different action |
| Break | Ends more than 2% short | Compared with the other fibers in the batch |
| Fiber attenuation | 0.40 dB/km at 1310, 0.25 at 1550 | Median section slope |

These are typical carrier values. Set them to your own acceptance spec.

It needs no splice map. A position where at least half the fibers show an event is treated as a splice point; an event anywhere else is flagged as unexpected. Span length is the median across the batch, so a short fiber stands out without anyone typing in the length.

Direction comes from the "location A" field inside each file, and fiber number and wavelength from the file too. File names are not used.

## Where the AI is, and where it isn't

The verdicts are fixed rules. A fiber that passes today passes tomorrow, and each failure states the number and the limit it broke.

The one model step is optional (`--ai`, or a button on the Claude-hosted demo): Claude turns the finished findings into a short note to the splicing contractor. It is given the findings only, never the traces, and told to add nothing.

I did consider having a model read the traces. For an acceptance test I would not trust it, and I could not defend it to a customer.

## The file reader

`triage/sor.py` reads the Bellcore/Telcordia SOR format (SR-4731), versions 1 and 2, written from the published block layout: what was tested, how, the event table and the trace. Vendor-specific blocks are skipped.

I checked it against three public sample files from real instruments (two version 1, one version 2): cable and fiber IDs, index of refraction, sample spacing, and every event's distance, loss, reflectance and slope match the published reference values to the third decimal. Those files belong to another project and are not redistributed here. The files in `examples/` are synthetic, written by the same module.

## What it does not do

- It uses the event table the OTDR saved in each file. It does not re-detect events from the raw trace, so it is only as good as the instrument's own analysis and thresholds.
- Launch and receive cable lengths must be set to match how the traces were shot. Get them wrong and the connector checks land on the wrong events.
- One span per batch. Mix two spans in one folder and the results mean nothing.
- Vendor formats other than `.sor` need exporting first.
- It does not replace your acceptance spec.

## Tests

```
pip install pytest
python -m pytest
```

Fifteen tests. One runs the browser version of the reader and the rules in Node across all 48 sample files and checks it matches the Python exactly, values and report text, so the demo cannot drift from the tool.

## Layout

```
triage/     sor (reader and writer), analyze (rules), report, narrative (optional AI note), CLI
examples/   the synthetic span: 48 .sor files
demo/       browser version (build with: python demo/build.py)
docs/       the built page, served by GitHub Pages
tests/
make_examples.py   regenerates the synthetic span
```
