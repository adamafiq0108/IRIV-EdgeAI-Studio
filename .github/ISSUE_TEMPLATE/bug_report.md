---
name: Bug report
about: Something in the product behaves wrong
labels: bug
---

## What happens, and what should happen

## Where

- [ ] hub
- [ ] master
- [ ] agent / device adapter
- [ ] detection app (`systems/*/app/`)
- [ ] frontend (React SPA)

## Reproduction

<!-- Smallest sequence that shows it. The mock device reproduces most things:
     python tools/mock_slave.py --port 5101 --token mock-token
     python master_server.py --system demo --port 5110 -->

## Evidence

<!-- The shortest decisive output, not a full log dump. If the device is
     involved, include the result of:
     python tools/contract_check.py http://<device>:5101 --token <token> -->

## Environment

- OS / Python / Node:
- Postgres reachable: yes / no  <!-- "everything Unassigned" and 503 on writes is expected when no -->
- Commit:
