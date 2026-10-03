import React from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";
import { CodeBlock } from "./CodeBlock";
import { DataTable, textOf } from "./DataTable";
import { ExternalLink } from "./env";
import { MermaidDiagram } from "./Mermaid";
import { FENCED_WIDGETS, widgetFromFence } from "./validate";
import { WidgetView } from "./WidgetView";

/**
 * Markdown for agent replies, with rich blocks:
 * - fenced code -> highlighted CodeBlock (file name, diff, copy)
 * - ```mermaid -> diagram; ```chart / ```timeline / ```steps / ```comparison / ```stat / ```map -> widgets
 *   (invalid JSON or schema falls back to a plain code block)
 * - $$…$$ -> KaTeX (single $ stays text, so prices like $5 are safe)
 * - tables -> sortable, copyable DataTable
 * - links open in the system browser
 */
export const RichMarkdown = React.memo(function RichMarkdown({ text, streaming = false }: { text: string; streaming?: boolean }) {
  return (
    <div className="chat-markdown lm-text">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, [remarkMath, { singleDollarTextMath: false }]]}
        rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: "ignore", output: "html" }]]}
        components={{
          a: ({ node: _node, href, children }) => <ExternalLink href={String(href || "")}>{children}</ExternalLink>,
          table: ({ node: _node, children }) => <DataTable>{children}</DataTable>,
          pre: ({ node: _node, children }) => <>{children}</>,
          code: ({ node, className, children }) => {
            const match = /language-([\w+#.:-]+)/.exec(className || "");
            const source = textOf(children).replace(/\n$/, "");
            const block = Boolean(match) || source.includes("\n");
            if (!block) return <code className={className}>{children}</code>;
            const lang = (match?.[1] || "").toLowerCase();
            const meta = String((node as { data?: { meta?: string } } | undefined)?.data?.meta || "");
            if (lang === "mermaid") return <MermaidDiagram code={source} streaming={streaming} />;
            if (FENCED_WIDGETS.has(lang)) {
              const widget = streaming ? null : widgetFromFence(lang, source);
              if (widget) return <WidgetView widget={widget} />;
              if (streaming) return <div className="wg-diagram-wait" role="status">Preparing {lang}…</div>;
            }
            return <CodeBlock code={source} lang={match?.[1] || ""} meta={meta} streaming={streaming} />;
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
});
