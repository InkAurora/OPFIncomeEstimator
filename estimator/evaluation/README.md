# Evaluation boundary

Evaluation code may compare completed predictions with physically isolated private truth. It must
never pass private fields back into estimator input or runtime features. Runtime code must never
import this directory.

Current automatic reports are produced by the simulator integration harness. Estimator-specific
segmentation and chart generation will be added here after the rule baseline is frozen.
