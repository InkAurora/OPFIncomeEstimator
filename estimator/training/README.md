# Training boundary

Training code may join observed features to isolated private labels. It must execute outside
`src/income_estimator`, split data by customer before fitting, enforce historical cutoffs, and emit
versioned model artifacts. Runtime code must never import this directory.

Estimator `0.1.0` has no trained models. This directory intentionally contains no Python package.
