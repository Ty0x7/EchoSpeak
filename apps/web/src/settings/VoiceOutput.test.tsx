import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ElevenLabsSettings, ELEVENLABS_MODELS } from "./VoiceOutput";

const save = async () => undefined;

describe("ElevenLabs read-aloud settings", () => {
  it("asks for the key first and says what reading aloud costs", () => {
    const html = renderToStaticMarkup(<ElevenLabsSettings s={{}} save={save} apiBase="http://api" />);
    expect(html.includes("Add your API key to see your voices.")).toBe(true);
    expect(html.includes("uses characters on your plan")).toBe(true);
    expect(html.includes("Play sample")).toBe(false); // no test button until there's a key
  });

  it("offers the test button and loads voices once a key is saved", () => {
    const html = renderToStaticMarkup(<ElevenLabsSettings s={{ elevenlabs_api_key: "***", elevenlabs_model: "eleven_v3" }} save={save} apiBase="http://api" />);
    expect(html.includes("Loading your voices")).toBe(true);
    expect(html.includes("Play sample")).toBe(true);
    expect(ELEVENLABS_MODELS.map((m) => m.value)).toEqual(["eleven_flash_v2_5", "eleven_turbo_v2_5", "eleven_multilingual_v2", "eleven_v3"]);
  });
});
