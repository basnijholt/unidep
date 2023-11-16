# Example

Run `requirements.yaml` files in subdirectories and combine them into an `environment.yaml` file.

Here we can just run `conda_join` with no arguments, since the defaults are the same as what we want.

This would be the same as running:

```bash
conda_join --directory . --depth 1 --output environment.yaml
```
or
```bash
conda_join -d . --depth 1 -o environment.yaml
```

See the resulting [`environment.yaml`](environment.yaml) file.
