/**
 * `Markdown` — render seguro de markdown GFM (tablas incluidas) para contenido
 * generado por agentes (p.ej. el reporte del análisis de Ads). react-markdown
 * NO interpreta HTML crudo (safe-by-default) y remark-gfm habilita las tablas
 * `| a | b |` que emiten los reporters.
 *
 * Estilado con clases Tailwind vía el mapping `components` (cero ediciones a
 * index.css); hereda el tamaño de fuente del contenedor.
 */
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function Markdown({ children }: { children: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-2 leading-relaxed [&_a]:text-accent [&_a]:underline">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children: c }) => (
            <h1 className="mt-1 text-base font-bold text-fg">{c}</h1>
          ),
          h2: ({ children: c }) => (
            <h2 className="mt-1 text-sm font-bold text-fg">{c}</h2>
          ),
          h3: ({ children: c }) => (
            <h3 className="mt-1 text-sm font-semibold text-fg">{c}</h3>
          ),
          p: ({ children: c }) => <p className="text-fg-soft">{c}</p>,
          strong: ({ children: c }) => (
            <strong className="font-semibold text-fg">{c}</strong>
          ),
          ul: ({ children: c }) => (
            <ul className="list-disc pl-5 text-fg-soft">{c}</ul>
          ),
          ol: ({ children: c }) => (
            <ol className="list-decimal pl-5 text-fg-soft">{c}</ol>
          ),
          code: ({ children: c }) => (
            <code className="rounded bg-line/50 px-1 font-mono text-[0.9em]">{c}</code>
          ),
          // La tabla del blend scrollea en su propio contenedor (no rompe el layout).
          table: ({ children: c }) => (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-xs">{c}</table>
            </div>
          ),
          th: ({ children: c }) => (
            <th className="border border-line bg-canvas px-2 py-1 text-left font-semibold text-fg">
              {c}
            </th>
          ),
          td: ({ children: c }) => (
            <td className="border border-line px-2 py-1 text-fg-soft">{c}</td>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
