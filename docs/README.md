# Documentation

Start with the [main README](../README.md) for what IRIV EdgeAI Studio is and how
to get it running. These pages go deeper.

## Using and installing

| Page | What it covers |
| --- | --- |
| [setup.md](setup.md) | Setting up a board: hub, systems, pairing, verification, troubleshooting |
| [install-appliance.md](install-appliance.md) | Installing onto an IRIV Edge AI device, start to finish |
| [configuration.md](configuration.md) | The `.env` file, the hotspot (AP mode) address, the hostname, and where settings are kept |
| [software-update.md](software-update.md) | Updating the software, online or with a manual update file |
| [features.md](features.md) | What the product does, feature by feature |

## Building on it

| Page | What it covers |
| --- | --- |
| [architecture.md](architecture.md) | Process model, data flow, config layering, storage, video, update layout |
| [api.md](api.md) | REST API reference for the hub, the system masters and the agent |
| [database-schema.md](database-schema.md) | The database schema and the TOML files that hold the rest of the state |
| [contract-v1.md](contract-v1.md) | How an AI system plugs into the dashboard |
| [conventions.md](conventions.md) | Code rules for contributors - layering, size limits, comments, what goes where |

Contributing? Read [CONTRIBUTING.md](../CONTRIBUTING.md). Found a security problem?
Report it privately as described in [SECURITY.md](../SECURITY.md).

## Keeping these true

Anything here that names a file, a route or a setting is a claim that can be
checked against the code. If you find one that has drifted, an issue or a pull
request fixing it is welcome.
