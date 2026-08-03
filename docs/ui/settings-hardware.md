# Settings — Hardware

## System summary
- CPU name, architecture, and live utilization
- Total RAM and current memory usage
- Total VRAM reported by the local GPU inventory

## GPU devices
- Per-GPU name, VRAM, index, and utilization
- Enable or disable devices used by managed engines
- **Limit Model Offload to Dedicated GPU Memory:** Toggle
- **Offload KV Cache to GPU Memory:** Toggle

## Multi-GPU rules
- **Single largest** — use the largest enabled GPU
- **Split evenly** — distribute layers evenly
- **Split by VRAM** — distribute according to available VRAM

## Live monitor
- RAM + VRAM usage bar
- CPU usage bar
- Per-GPU utilization and memory readings
- Refreshes while Settings is open

Hardware values come from the current machine through the backend. The
documentation intentionally does not contain fixed example hardware.
