import React from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import type { PluggableList } from "unified";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";
import { CodeBlock } from "./CodeBlock";
import { DataTable, textOf } from "./DataTable";
import { ResearchLink } from "./env";
import { SafeImage } from "./SafeImage";
import { MermaidDiagram } from "./Mermaid";
import { endsInOpenFence, normalizeMathDelimiters } from "./markdownPrep";
import { FENCED_WIDGETS, widgetFromFence } from "./validate";
import { WidgetView } from "./WidgetView";

/**
 * Markdown for agent replies, with rich blocks:
 * - fenced code -> highlighted CodeBlock (file name, diff, copy)
 * - ```mermaid -> diagram; ```chart / ```timeline / ```steps / ```comparison / ```stat / ```map -> widgets
 *   (invalid JSON or schema falls back to a plain code block)
 * - $$…$$, \( … \) and \[ … \] -> KaTeX (single $ stays text, so prices like $5 are safe)
 * - while streaming, a diagram or widget draws as soon as its fence closes; only the block still
 *   being written waits
 * - tables -> sortable, copyable DataTable
 * - links open in the system browser
 */
const REMARK_PLUGINS = [remarkGfm, [remarkMath, { singleDollarTextMath: false }]] as const;
const REHYPE_PLUGINS = [[rehypeKatex, { throwOnError: false, strict: "ignore", output: "html" }]] as const;

type Live = {
  markdown: string; openTail: boolean;
  renderImage?(src: string, alt: string): React.ReactNode;
  renderLink?(href: string, children: React.ReactNode): React.ReactNode;
};

export const RichMarkdown = React.memo(function RichMarkdown({ text, streaming = false, renderImage, renderLink }: {
  text: string; streaming?: boolean;
  renderImage?(src: string, alt: string): React.ReactNode;
  renderLink?(href: string, children: React.ReactNode): React.ReactNode;
}) {
  const markdown = React.useMemo(() => normalizeMathDelimiters(text), [text]);
  const live = React.useRef<Live>({ markdown, openTail: false });
  live.current = { markdown, openTail: streaming && endsInOpenFence(markdown), renderImage, renderLink };
  // One stable set of renderers for the life of the message. A new object per streamed token
  // would be a new component type to React, remounting every diagram, chart and code block on
  // each token, so a diagram could never finish drawing while the reply was still streaming.
  const components = React.useMemo<Components>(() => ({
    a: ({ node: _node, href, children }) => live.current.renderLink ? live.current.renderLink(String(href || ""), children) : <ResearchLink href={String(href || "")}>{children}</ResearchLink>,
    img: ({ node: _node, src, alt }) => live.current.renderImage ? live.current.renderImage(String(src || ""), String(alt || "")) : <SafeImage src={typeof src === "string" ? src : undefined} alt={alt} />,
    table: ({ node: _node, children }) => <DataTable>{children}</DataTable>,
    pre: ({ node: _node, children }) => <>{children}</>,
    code: ({ node, className, children }) => {
      const match = /language-([\w+#.:-]+)/.exec(className || "");
      const source = textOf(children).replace(/\n$/, "");
      const block = Boolean(match) || source.includes("\n");
      if (!block) return <code className={className}>{children}</code>;
      const lang = (match?.[1] || "").toLowerCase();
      const meta = String((node as { data?: { meta?: string } } | undefined)?.data?.meta || "");
      // Still being written: the reply is streaming, it ends in an open fence, and this block is that last one.
      const { markdown: current, openTail } = live.current;
      const end = Number((node as { position?: { end?: { offset?: number } } } | undefined)?.position?.end?.offset ?? source.length);
      const writing = openTail && end >= current.trimEnd().length - 1;
      if (lang === "mermaid") return <MermaidDiagram code={source} streaming={writing} />;
      if (FENCED_WIDGETS.has(lang)) {
        const widget = writing ? null : widgetFromFence(lang, source);
        if (widget) return <WidgetView widget={widget} />;
        if (writing) return <div className="wg-diagram-wait" role="status">Preparing {lang}…</div>;
      }
      return <CodeBlock code={source} lang={match?.[1] || ""} meta={meta} streaming={writing} />;
    },
  }), []);
  return (
    <div className="chat-markdown lm-text">
      <ReactMarkdown
        remarkPlugins={REMARK_PLUGINS as unknown as PluggableList}
        rehypePlugins={REHYPE_PLUGINS as unknown as PluggableList}
        components={components}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
});
