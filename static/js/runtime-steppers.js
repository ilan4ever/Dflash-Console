/** Runtime inspector +/- steppers with logical value ladders */
(function () {
  const CONTEXT_LADDER = [2048, 4096, 8192, 16384, 32768, 65536, 131072, 262144];
  const TOP_K_LADDER = [0, 10, 20, 30, 40, 50, 64, 80, 100, 128, 160, 200];
  const MAX_TOKEN_LADDER = [256, 512, 1024, 2048, 4096, 8192, 16384, 32768];

  function stepLadder(value, steps, direction, min, max) {
    const sorted = [...new Set(steps)].filter((v) => v >= min && v <= max).sort((a, b) => a - b);
    if (!sorted.length) return min;
    let num = Number(value);
    if (Number.isNaN(num)) num = sorted[0];
    num = Math.min(max, Math.max(min, num));
    if (direction > 0) {
      const next = sorted.find((v) => v > num);
      return next != null ? next : sorted[sorted.length - 1];
    }
    const prev = [...sorted].reverse().find((v) => v < num);
    return prev != null ? prev : sorted[0];
  }

  function contextSteps(max) {
    const cap = Number(max) || 262144;
    return CONTEXT_LADDER.filter((v) => v >= 2048 && v <= cap);
  }

  function gpuLayerSteps(max) {
    const cap = Math.max(0, Number(max) || 128);
    const steps = [0];
    for (let value = 8; value < 96; value += 8) steps.push(value);
    if (cap >= 99) steps.push(99);
    for (let value = 104; value <= cap; value += 8) steps.push(value);
    if (cap >= 128 && !steps.includes(128)) steps.push(128);
    return [...new Set(steps)].filter((v) => v <= cap).sort((a, b) => a - b);
  }

  function batchSteps(min, max) {
    const steps = [];
    for (let value = 32; value <= max; value *= 2) {
      if (value >= min) steps.push(value);
    }
    return steps;
  }

  function floatSteps(min, max, increment) {
    const steps = [];
    const count = Math.round((max - min) / increment);
    for (let i = 0; i <= count; i += 1) {
      steps.push(Number((min + i * increment).toFixed(4)));
    }
    return steps;
  }

  function cpuThreadSteps(max) {
    const cap = Math.max(1, Number(max) || 64);
    return Array.from({ length: cap }, (_, index) => index + 1);
  }

  function clampInput(input) {
    const min = Number(input.min ?? 0);
    const max = Number(input.max ?? Number.MAX_SAFE_INTEGER);
    let value = Number(input.value);
    if (Number.isNaN(value)) value = min;
    value = Math.min(max, Math.max(min, value));
    if (input.step && String(input.step).includes('.')) {
      const decimals = String(input.step).split('.')[1]?.length || 2;
      input.value = value.toFixed(decimals);
    } else {
      input.value = String(Math.round(value));
    }
    return Number(input.value);
  }

  function bindStepper(inputId, getSteps, onChange) {
    const input = document.getElementById(inputId);
    const wrap = input?.closest('.lm-stepper');
    if (!input || !wrap) return;
    const applyStep = (direction) => {
      const min = Number(input.min ?? 0);
      const max = Number(input.max ?? Number.MAX_SAFE_INTEGER);
      const steps = getSteps(input);
      const next = stepLadder(input.value, steps, direction, min, max);
      if (input.step && String(input.step).includes('.')) {
        const decimals = String(input.step).split('.')[1]?.length || 2;
        input.value = Number(next).toFixed(decimals);
      } else {
        input.value = String(next);
      }
      input.dispatchEvent(new Event('input', { bubbles: true }));
      if (onChange) onChange(input);
    };
    wrap.querySelectorAll('.lm-stepper-btn').forEach((btn) => {
      btn.addEventListener('mousedown', (event) => {
        event.preventDefault();
      });
      btn.addEventListener('click', () => {
        const direction = Number(btn.dataset.step || 0);
        if (direction) applyStep(direction);
      });
    });
    input.addEventListener('change', () => {
      clampInput(input);
    });
  }

  function bindInspectorSteppers() {
    bindStepper('inspectorContext', (input) => contextSteps(input.max));
    bindStepper('inspectorContextMax', (input) => contextSteps(input.max));
    bindStepper('inspectorGpuLayers', (input) => gpuLayerSteps(input.max));
    bindStepper('inspectorCpuThreads', (input) => cpuThreadSteps(input.max));
    bindStepper('inspectorEvalBatch', (input) => batchSteps(Number(input.min || 32), Number(input.max || 8192)));
    bindStepper('inspectorPhysicalBatch', (input) => batchSteps(Number(input.min || 32), Number(input.max || 8192)));
    bindStepper('inspectorTemperature', () => floatSteps(0, 2, 0.05));
    bindStepper('inspectorTopP', () => floatSteps(0, 1, 0.05));
    bindStepper('inspectorTopK', (input) => TOP_K_LADDER.filter((v) => v <= Number(input.max || 200)));
    bindStepper('inspectorRepeatPenalty', () => floatSteps(1, 2, 0.05));
    bindStepper('inspectorMaxTokens', (input) => {
      const max = Number(input.max || 32768);
      return MAX_TOKEN_LADDER.filter((v) => v <= max);
    });
  }

  window.DFlashRuntimeSteppers = {
    bindInspectorSteppers,
    contextSteps,
    gpuLayerSteps,
  };
})();
