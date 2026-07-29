/** Shared model-type filter options for library scan & browse modals */
(function () {
  window.DFlashLibraryTypes = {
    options: [
      { id: 'dflash', label: 'DFlash' },
      { id: 'gguf', label: 'GGUF checkpoints' },
      { id: 'tts', label: 'Text-to-speech' },
      { id: 'speech', label: 'Speech-to-text' },
      { id: 'ocr', label: 'OCR' },
      { id: 'embeddings', label: 'Embeddings' },
      { id: 'custom', label: 'All model types' },
    ],
    title(id) {
      const row = this.options.find((item) => item.id === id);
      return row?.label || 'All model types';
    },
  };
})();
