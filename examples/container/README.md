# Container layering example

Export `.depfix/imports.lock` on a matching CPython 3.13 Linux target before building. The manifest/install layer changes
only when package declarations change; application source can change independently. For an offline image build, transfer a
`.depfixbundle` and install it with `--offline --frozen` instead of reaching an index.

From the repository root after Depfix 0.2.0 is published:

```bash
depfix export examples/container -o .depfix/imports.lock
docker build -t depfix-container-example examples/container
docker run --rm depfix-container-example
```
