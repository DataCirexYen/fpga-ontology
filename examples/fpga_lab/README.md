# FPGA lab example

This folder is a complete bring-up ontology for a small FPGA lab. The CSV and JSON files are the system of record; the loader is idempotent and never touches evidence.

```sh
python3 examples/fpga_lab/load.py            # into data/workbench.db
python3 examples/fpga_lab/load.py --db lab.db
```

Object types: `Host` (build and lab machines), `Board` (Alveo U55C, Alveo U200, Kria KV260, each with a power cap), `Kernel` (RTL or HLS design with its golden model), `Build` (one synthesis of a kernel for a board), `Gate` (G0 simulation to G4 silicon validated), `Requirement` (binding rules and release holds), `WorkOrderStep` (the numbered bring-up steps). Links: `installed_in`, `builds`, `targets`, `requires`, `governed_by`, `touches`, `released_by`.

Six record actions show every criterion kind: **Record synthesis result** and **Record regression run** on `Build` (notes required on failure), **Record power measurement** on `Board` (refused above the board's power cap), **Set release-hold status** on `Requirement` (holds only, evidence required to release), **Bring-up step control** on `WorkOrderStep` (PROCEED / STOP / ROLL BACK state machine), and **Record gate result** on `Gate` (the preceding gate must have passed).

Copy the folder, replace the files with your own boards, kernels, and gates, and keep `load.py` as the template for your loader. Every object in the example is linked to at least one other; `tests/test_fpga_lab.py` in the repository root enforces that, which is a useful check to keep in your own pack.
