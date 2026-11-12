# CLI help evidence

Commit: 77e24c3e490a11721041a40ad4bd203f51f84039. Executed only argparse `--help` on downloaded read-only snapshots; no model, freeze, registry mutation or server operation.

## python -m p0.p2.cli --help

Exit: 0

```text
usage: cli.py [-h] --run-dir RUN_DIR [--registry REGISTRY]
              {freeze,preflight,probe,score,replay,accept,report} ...

P2 B0 reproduction and bounded B1 offline screening commands.

positional arguments:
  {freeze,preflight,probe,score,replay,accept,report}

options:
  -h, --help            show this help message and exit
  --run-dir RUN_DIR
  --registry REGISTRY
```

## python -m p0.p2.cli --run-dir /tmp/p3-help-only freeze --help

Exit: 0

```text
usage: cli.py freeze [-h] [--phase {B0,B1,B2,B3,B4}]

options:
  -h, --help            show this help message and exit
  --phase {B0,B1,B2,B3,B4}
```

## python -m p0.p2.cli --run-dir /tmp/p3-help-only preflight --help

Exit: 0

```text
usage: cli.py preflight [-h] --experiment EXPERIMENT

options:
  -h, --help            show this help message and exit
  --experiment EXPERIMENT
```

## python -m p0.p2.cli --run-dir /tmp/p3-help-only probe --help

Exit: 0

```text
usage: cli.py probe [-h] --experiment EXPERIMENT --mode
                    {fixed-cuts,fixed-text,causal-stream}

options:
  -h, --help            show this help message and exit
  --experiment EXPERIMENT
  --mode {fixed-cuts,fixed-text,causal-stream}
```

## python -m p0.p2.cli --run-dir /tmp/p3-help-only score --help

Exit: 0

```text
usage: cli.py score [-h] --experiment EXPERIMENT

options:
  -h, --help            show this help message and exit
  --experiment EXPERIMENT
```

## python -m p0.p2.cli --run-dir /tmp/p3-help-only replay --help

Exit: 0

```text
usage: cli.py replay [-h] --experiment EXPERIMENT --repeat {1,2}

options:
  -h, --help            show this help message and exit
  --experiment EXPERIMENT
  --repeat {1,2}
```

## python -m p0.p2.cli --run-dir /tmp/p3-help-only accept --help

Exit: 0

```text
usage: cli.py accept [-h] --experiment EXPERIMENT --stage
                     {engineering,holdout}

options:
  -h, --help            show this help message and exit
  --experiment EXPERIMENT
  --stage {engineering,holdout}
```

## python -m p0.p2.cli --run-dir /tmp/p3-help-only report --help

Exit: 0

```text
usage: cli.py report [-h]

options:
  -h, --help  show this help message and exit
```

## python -m p0.p2.b3_vox_cache --help

Exit: 0

```text
usage: b3_vox_cache.py [-h] --run-dir RUN_DIR [--capability] [--worker]

One registered Vox deployment diagnosis: bound MLX free cache at512MiB.
Existing B3 inference source stays immutable. This launcher changes allocator
policy only; its own source and policy must be frozen in a fresh attempt
before any model output.

options:
  -h, --help         show this help message and exit
  --run-dir RUN_DIR
  --capability
  --worker
```

## python -m p0.p2.b3_worker --help

Exit: 0

```text
usage: b3_worker.py [-h] --run-dir RUN_DIR --experiment
                    {NATIVE-VOX-960,AST-G4-OFFICIAL} [--capability]

Isolated B3 native-audio capability and offline probes; one MLX owner.

options:
  -h, --help            show this help message and exit
  --run-dir RUN_DIR
  --experiment {NATIVE-VOX-960,AST-G4-OFFICIAL}
  --capability
```


