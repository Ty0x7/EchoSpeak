// Convert microphone samples to 20 ms, 16 kHz mono PCM16 frames off the UI thread.
class EchoLiveMicrophone extends AudioWorkletProcessor {
  constructor() {
    super();
    this.frame = new Int16Array(320);
    this.position = 0;
    this.sum = 0;
    this.count = 0;
    this.phase = 0;
  }
  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;
    for (const value of input) {
      this.sum += value;
      this.count++;
      this.phase += 16000;
      if (this.phase >= sampleRate) {
        this.phase -= sampleRate;
        const sample = Math.max(-1, Math.min(1, this.sum / this.count));
        this.frame[this.position++] = Math.round(sample * (sample < 0 ? 32768 : 32767));
        this.sum = this.count = 0;
        if (this.position === this.frame.length) {
          this.port.postMessage(this.frame.buffer, [this.frame.buffer]);
          this.frame = new Int16Array(320);
          this.position = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor("echospeak-live-microphone", EchoLiveMicrophone);
