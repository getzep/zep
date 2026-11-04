# Zep Agent Skills benchmarks

These benchmarks test whether an agent can use a compiled skill to complete a
task. A skill is a reusable procedure. Zep compiles skills from traces. The
benchmark runs then search for relevant skills or place selected skills in the
agent prompt.

| Harness | Benchmark |
|---|---|
| [Tau3 Banking and AppWorld](tau3-appworld/README.md) | Banking tasks in tau3 and scenario tasks in AppWorld |
| [EnterpriseOps-Gym](enterpriseops-gym/README.md) | Enterprise tool-use tasks with ServiceNow data |
| [ALFWorld](alfworld/README.md) | Text-based household tasks and transfer to unseen rooms |

Each package has a `pyproject.toml`, a `uv.lock`, tests, and run instructions.
The harnesses use `zep-cloud==4.1.0`. Live model and Zep calls are not part of
the local test suite.
