/**
 * Sidebar de Upload Productos: lista de jobs (en curso + recientes).
 */

import { useImportJobs, type ImportJob } from "@/entities/import-job";
import { Icon, MacButton } from "@/shared/ui";

interface Props {
  selectedJobId: string;
  onSelect: (id: string) => void;
}

export function UploadJobs({ selectedJobId, onSelect }: Props) {
  const { data: jobs = [] } = useImportJobs();
  const inProgress = jobs.filter((j) => j.status === "draft" || j.status === "live");
  const recent = jobs.filter((j) => j.status !== "draft" && j.status !== "live");

  return (
    <aside className="sidebar">
      <div className="sb-head">
        <h2>Importaciones</h2>
        <MacButton primary sm>+ Nueva</MacButton>
      </div>

      <div className="sb-search">
        <Icon.search />
        <input placeholder="Buscar trabajos…" />
      </div>

      <div className="sb-section">
        <div className="sb-section-h"><span>En curso</span></div>
        {inProgress.map((j) => (
          <JobRow
            key={j.id}
            job={j}
            selected={selectedJobId === j.id}
            onSelect={onSelect}
          />
        ))}
      </div>

      <div className="sb-section">
        <div className="sb-section-h"><span>Recientes</span></div>
        {recent.map((j) => (
          <JobRow
            key={j.id}
            job={j}
            selected={selectedJobId === j.id}
            onSelect={onSelect}
          />
        ))}
      </div>

      <div className="sb-foot">
        <span>{jobs.length} trabajos · 12.4 MB</span>
        <button className="ico-btn" title="Refrescar">
          <Icon.refresh />
        </button>
      </div>
    </aside>
  );
}

interface JobRowProps {
  job: ImportJob;
  selected: boolean;
  onSelect: (id: string) => void;
}

function JobRow({ job, selected, onSelect }: JobRowProps) {
  const ico =
    job.status === "draft" ? <Icon.edit /> :
    job.status === "live" ? <Icon.upload /> :
    job.status === "ok" ? <Icon.check /> : <Icon.alert />;
  const statusClass =
    job.status === "live" ? "live" :
    job.status === "ok" ? "ok" :
    job.status === "err" ? "err" : "";
  return (
    <div
      className={"job-row" + (selected ? " sel" : "")}
      onClick={() => onSelect(job.id)}
    >
      <div className={"jico " + statusClass}>{ico}</div>
      <div className="jb">
        <div className="jn">{job.name}</div>
        <div className="jm">
          {job.count} productos · <span className="jt">{job.when}</span>
        </div>
      </div>
      {job.status === "live" && <span className="conf med">{job.progress}%</span>}
      {job.status === "err" && <span className="conf low">{job.progress}%</span>}
      {job.status === "draft" && <span className="conf skip">Borrador</span>}
    </div>
  );
}
