# Install the Otter CLI

Otter requires Git and Node.js 20 or later.
The CLI is not published to npm yet, so install it from its GitHub repository.

## Install from GitHub

Clone the repository, install its dependencies, build the CLI, and link the CLI workspace package globally:

```bash
git clone https://github.com/narumiruna/otter.git
cd otter
npm install
npm run build:cli
npm link --workspace @narumitw/otter
```

Confirm that the `otter` executable is available:

```bash
otter --help
```

## Update an Existing Installation

Pull the latest source, refresh dependencies, rebuild, and link the CLI again:

```bash
cd otter
git pull --ff-only
npm install
npm run build:cli
npm link --workspace @narumitw/otter
```
