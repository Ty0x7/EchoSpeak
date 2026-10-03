import React, { useMemo, useState } from "react";
import { CopyButton } from "./CodeBlock";

/** Plain text of rendered markdown children (for sorting and copying). */
export function textOf(node: React.ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (React.isValidElement(node)) return textOf((node.props as { children?: React.ReactNode }).children);
  return "";
}

const childrenOf = (node: React.ReactNode): React.ReactElement[] =>
  React.Children.toArray(React.isValidElement(node) ? (node.props as { children?: React.ReactNode }).children : node).filter(React.isValidElement) as React.ReactElement[];

/** Compare cells: numbers (incl. $, %, commas) numerically, otherwise text. */
export function compareCells(a: string, b: string): number {
  const na = Number(a.replace(/[$,%\s]/g, ""));
  const nb = Number(b.replace(/[$,%\s]/g, ""));
  if (a.trim() && b.trim() && Number.isFinite(na) && Number.isFinite(nb)) return na - nb;
  return a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
}

/**
 * Markdown tables, made sortable and copyable. Rendering keeps each cell's
 * formatted content (links, bold); sorting uses its text.
 */
export function DataTable({ children }: { children: React.ReactNode }) {
  const sections = React.Children.toArray(children).filter(React.isValidElement) as React.ReactElement[];
  const head = sections.find((s) => s.type === "thead");
  const body = sections.find((s) => s.type === "tbody");
  const headCells = head ? childrenOf(childrenOf(head)[0]) : [];
  const rows = body ? childrenOf(body).map((row) => childrenOf(row)) : [];
  const [sort, setSort] = useState<{ col: number; dir: 1 | -1 } | null>(null);
  const order = useMemo(() => {
    const idx = rows.map((_, i) => i);
    if (!sort) return idx;
    return idx.sort((a, b) => compareCells(textOf(rows[a][sort.col]), textOf(rows[b][sort.col])) * sort.dir);
  }, [rows, sort]);
  const headers = headCells.map((c) => textOf(c));
  const tsv = [headers, ...rows.map((r) => r.map((c) => textOf(c)))].map((r) => r.join("\t")).join("\n");
  const md = [`| ${headers.join(" | ")} |`, `| ${headers.map(() => "---").join(" | ")} |`, ...rows.map((r) => `| ${r.map((c) => textOf(c).replace(/\|/g, "\\|")).join(" | ")} |`)].join("\n");
  return (
    <div className="wg wg-datatable">
      <div className="wg-table-tools">
        <span>{rows.length} row{rows.length === 1 ? "" : "s"}</span>
        <CopyButton text={tsv} label="Copy for Sheets" />
        <CopyButton text={md} label="Copy Markdown" />
      </div>
      <div className="wg-table-wrap">
        <table className="wg-table">
          <thead>
            <tr>
              {headCells.map((cell, i) => {
                const active = sort?.col === i;
                return (
                  <th key={i} scope="col" aria-sort={active ? (sort!.dir === 1 ? "ascending" : "descending") : "none"}>
                    <button type="button" className="wg-sort" onClick={() => setSort(active ? (sort!.dir === 1 ? { col: i, dir: -1 } : null) : { col: i, dir: 1 })}>
                      {(cell.props as { children?: React.ReactNode }).children}
                      <span aria-hidden>{active ? (sort!.dir === 1 ? " ▲" : " ▼") : ""}</span>
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {order.map((r) => (
              <tr key={r}>
                {rows[r].map((cell, c) => (
                  <td key={c}>{(cell.props as { children?: React.ReactNode }).children}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
