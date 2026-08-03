# Playground

Chat preferences are not a separate Settings panel in the current build. The
Playground provides the supported controls directly:

- Create and switch between local chat sessions.
- Choose a source, engine, and model.
- Load the selected model before sending a prompt.
- Attach images or text/code files.
- Press **Enter** to send and **Shift+Enter** for a newline.
- Clear the active conversation.

The Playground works with DFlash profiles, LM Studio files, and other local
GGUF models. Chat requests use the Console proxy and display streaming output
when the selected engine supports it.
