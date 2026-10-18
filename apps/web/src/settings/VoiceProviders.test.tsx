import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { VoiceProviders, SPEECH_PROVIDERS } from "./VoiceProviders";
import { DramaSettings } from "./DramaSettings";
import { OptionalCapabilities } from "../setup/OptionalCapabilities";

const save = async () => undefined;
describe("Optional native voice setup", () => {
  it("retains all existing providers and adds Drama without replacing the conversation model", () => {
    expect(SPEECH_PROVIDERS.map(p => p.value)).toEqual(["windows-sapi", "piper-local", "drama3-local", "elevenlabs"]);
    const html = renderToStaticMarkup(<VoiceProviders s={{}} save={save} apiBase="http://api" />);
    expect(html.includes("Your AI model still handles the conversation")).toBe(true);
    expect(html.includes("Windows default")).toBe(true);
    expect(html.includes("API key")).toBe(false);
  });
  it("does not fetch or expose cloud credentials when a local provider is selected", () => {
    const html = renderToStaticMarkup(<VoiceProviders s={{ voice_local_tts_provider: "piper-local", elevenlabs_api_key: "secret" }} save={save} apiBase="http://api" />);
    expect(html.includes("Voice model")).toBe(true);
    expect(html.includes("secret")).toBe(false);
    expect(html.includes("Loading your voices")).toBe(false);
  });
  it("requires a license and explains runtime and memory limits before installation", () => {
    const html = renderToStaticMarkup(<DramaSettings s={{}} save={save} apiBase="http://api" />);
    expect(html.includes("Built with Fish Audio")).toBe(true);
    expect(html.includes("I have read and accept")).toBe(true);
    expect(html.includes("audiocpp_cli.exe")).toBe(true);
    expect(html.includes("Download size is not RAM or VRAM use")).toBe(true);
    expect(html.includes("No system tools are installed automatically")).toBe(true);
    expect(html.includes("disabled")).toBe(true);
  });
  it("first-run optional features start collapsed without a model download prompt", () => {
    const html = renderToStaticMarkup(<OptionalCapabilities s={{}} save={save} apiBase="http://api" />);
    expect(html.includes("Set up voice")).toBe(true);
    expect(html.includes("Set up creations")).toBe(true);
    expect(html.includes("Download / Resume")).toBe(false);
    expect(html.includes("API key")).toBe(false);
  });
});
