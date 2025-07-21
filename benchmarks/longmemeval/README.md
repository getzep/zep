# Zep: A Temporal Knowledge Graph Architecture for Agent Memory Experiments
For this paper, experiments are contained in two notebooks:
* One for the DMR experiment first presented in the MemGPT paper
* A second for the LongMemEval experiment

The notebooks will walk through downloading the datasets and building the necessary objects

OpenAI and Zep keys are required to run both experiments.

The notebook cells will first walk through ingesting data into Zep, and the evaluating Zep.
In the case of LongMemEval, results are saved in a JSON file.


Both notebooks also contain a cell to run the experiment against a baseline.
Models will default to gpt-4o-mini but can be updated in code.