# DFlash Console v0.3.300

## In-app update fix

- The update no longer fails with **"File copy failed (code 11)"** when the old app takes a few seconds to close. The installer now waits for DFlash Console to release its files and retries the copy.
- If files are still in use, it says so and offers **Retry** instead of stopping.
- If the 0.3.299 update failed for you, install 0.3.300 (in-app update, or the setup below). Your settings and models are kept.

## Also in this release (from 0.3.299)

0.3.299 was paused because of the update problem above, so its changes reach most users with 0.3.300.
### Sign-in and machine keys

- **Settings → Sign-in & keys** — set an owner password, see signed-in browsers, and create a machine key for each computer that should use this PC's GPU. A key is shown once, can be revoked on its own, and may download models here only if you turn on **May download models here** (Hugging Face files only, within a disk limit).
- **Nothing changes on one PC.** The Console and the OpenAI gateway still listen only on this PC, and programs on this PC never need to sign in.
- **Other computers** must sign in (browser) or send a machine key (another Console). Shutdown, file browsing, config import and export, engine install and uninstall, and anything that runs a program never work from another computer.
- **Reverse proxies:** a request relayed by a proxy on this PC (`tailscale serve`, nginx, cloudflared) now counts as another computer. If you reached your Console that way, set an owner password and sign in.
- **Nodes:** the token field is now **Machine key**, and each node has **Edit** to add or change it.

### Linux

- The Console runs on Linux: `install-linux.sh` sets it up in one step; llama-server, vLLM, Transformers, and the speech runtimes (whisper-server, Piper, faster-whisper, VibeVoice) install with `.sh` scripts.
- The stats bar, Hugging Face catalog, and **Settings → Downloads & engines** work on Linux, with Linux wording.
- New downloads go to the `models` folder (a Windows-style `.\models` in `config.json` is read correctly).
- Starting the Console no longer stops other programs that listen on a local port, such as another Console or LM Studio.

### Everyday fixes

- Much less background polling while the app is idle or hidden.
- The page no longer reloads after one slow health check; **Offline** shows only after two missed checks.
- Engine install progress no longer jumps to 87% while pip updates itself.
- Hugging Face catalog search error fixed.
- vLLM shows Qwen3's thinking apart from the answer.
- Windows startup is registered only once.
- `pip install dflash-console` again includes everything the OpenAI gateway (`:8001`) needs.

## Install

- Windows: [GitHub Releases](https://github.com/ilan4ever/Dflash-Console/releases/tag/v0.3.300)
- PyPI: `pip install dflash-console==0.3.300`
