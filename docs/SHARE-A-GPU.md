# Share a GPU between two DFlash Consoles

You can load a model on this computer and have it run on a GPU in another computer that also has DFlash Console. The other GPU shows up in the model list with that computer’s name in brackets, for example **TITAN (lab)**.

The model runs on the computer that owns the GPU. This computer only sends the request. If the model file is not on the other computer yet, DFlash Console downloads it there and then loads it.

## What you need

- DFlash Console running on both computers.
- The other computer turned on.
- A way for this computer to reach the other Console:
  - same home network: use its address, such as `http://192.168.1.50:8900`
  - Tailscale or SSH: use **Nodes → Connect securely**

## Do this on the computer where you pick models

1. Open **Nodes**.
2. Click **Add node**, or **Connect securely** if the other computer is not already on your network.
3. Enter a name and the other Console’s address.
4. Leave **Use this computer’s GPU** turned on.
5. Add the node. Its card should say **GPU shared with this PC**. If it does not, click **Share GPU**.
6. Open **Models** or **Engines** and load a model.
7. In the GPU list, pick the name that ends with the other computer in brackets, such as **TITAN (lab)**.

That load runs on the other computer. A later GPU list from this Console includes that GPU as well as the ones in this PC.

## If you only want to check the other computer

Turn **Use this computer’s GPU** off, or click **Stop sharing GPU**. You can still check that it is online and send a test chat. Its GPU will not appear in the model list.

## Good to know

- Both Consoles must stay running while you use the shared GPU.
- The bracket name is the node name, so pick a short name you will recognize.
- A model that is one downloadable file can be fetched onto the other computer automatically. A folder that is not a single file must already be on that computer.
- SSH is only needed when you cannot open the other Console’s address directly. On a normal home network, the address is enough.
