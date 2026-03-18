"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { PipelineOut } from "@/types";
import { ThemeToggle } from "@/components/ThemeToggle";
import { ArrowRight, BoxSelect, Search, Plus, Activity } from "lucide-react";
import styles from "./LandingPage.module.css";
import { TEMPLATES, type Template } from "@/features/pipeline/templates/templates";


/*  Sub-components  */

function TemplateCard({ template, onClick }: { template: Template; onClick: () => void }) {
  const meta = template.meta;

  return (
    <button className={`${styles.cardButton} ${styles.templateCard}`} onClick={onClick} type="button">
      <div className={styles.cardTop}>
        <div
          className={styles.cardIcon}
          style={{ background: `${meta.color}16`, color: meta.color }}
        >
          {meta.icon}
        </div>

        <span
          className={styles.cardTag}
          style={{
            color: meta.color,
            background: `${meta.color}14`,
            border: `1px solid ${meta.color}28`,
          }}
        >
          {meta.tag}
        </span>
      </div>

      <div className={styles.cardBody}>
        <div className={styles.cardTitle}>{template.name}</div>
        <p className={styles.cardText}>{template.description}</p>

        <div className={styles.cardMeta}>
          <span className={styles.metaPill}>
            {template.config.nodes.length} nodes
          </span>
          <span className={styles.metaPill}>
            {template.config.edges.length} edges
          </span>
        </div>
      </div>

      <div className={styles.cardFooter}>
        <span className={styles.cardFooterLink}>
          Use template <ArrowRight size={14} />
        </span>
      </div>
    </button>
  );
}

function SavedWorkflowCard({ pipeline, onClick }: { pipeline: PipelineOut; onClick: () => void }) {
  return (
    <button className={`${styles.cardButton} ${styles.savedCard}`} onClick={onClick} type="button">
      <div className={styles.cardTop}>
        <div className={styles.cardIcon} style={{ background: "var(--bg-elevated)", color: "var(--text-muted)" }}>
          <Activity size={20} />
        </div>
        <span className={styles.cardTag} style={{ color: "var(--text-dim)", background: "var(--bg-elevated)", border: "1px solid var(--border)" }}>
          Saved
        </span>
      </div>
      <div className={styles.cardBody}>
        <div className={styles.cardTitle}>{pipeline.name}</div>
        <p className={styles.cardText}>
          Updated {new Date(pipeline.updated_at).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}
        </p>
      </div>
      <div className={styles.cardFooter}>
        <span className={styles.cardFooterLink}>
          Open workflow <ArrowRight size={14} />
        </span>
      </div>
    </button>
  );
}

/*  Main page  */

export function LandingPage({ onCreateBlank, onLoadTemplate, onLoadPipeline }: {
  onCreateBlank: () => void;
  onLoadTemplate: (template: Template["config"]) => void;
  onLoadPipeline: (pipeline: PipelineOut) => void;
}) {
  const [saved, setSaved] = useState<PipelineOut[]>([]);
  const [search, setSearch] = useState("");
  const templatesRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    api.listPipelines().then(setSaved).catch(() => {});
  }, []);

  const filteredTemplates = useMemo(
    () =>
      TEMPLATES.filter(
        (t) =>
          !search ||
          t.name.toLowerCase().includes(search.toLowerCase()) ||
          t.description.toLowerCase().includes(search.toLowerCase()),
      ),
    [search],
  );

  return (
    <div className={styles.page}>
      {/*  Header  */}
      <header className={styles.header}>
        <div className={styles.headerInner}>
          <div className={styles.brand}>
            <div className={styles.brandMark}>
              <BoxSelect size={20} />
            </div>
            <div className={styles.brandCopy}>
              <div className={styles.brandRow}>
                <span className={styles.brandTitle}>Agent Builder</span>
                <span className={styles.beta}>Beta</span>
              </div>
              <span className={styles.brandSubtitle}>Design agent workflows with clean architecture</span>
            </div>
          </div>
          <ThemeToggle />
        </div>
      </header>

      {/*  Main scrollable area  */}
      <main className={styles.main}>
        <div className={styles.container}>

          {/*  Templates  */}
          <section ref={templatesRef} className={styles.section}>
            <div className={styles.sectionHeader}>
              <div>
                <h2 className={styles.sectionTitle}>Templates</h2>
                <p className={styles.sectionDescription}>
                  Pick a starting point or create from scratch.
                </p>
              </div>
              <div className={styles.sectionAction}>
                <div className={styles.searchWrap}>
                  <Search size={16} className={styles.searchIcon} />
                  <input
                    className={styles.searchInput}
                    placeholder="Search templates"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </div>
              </div>
            </div>

            <div className={styles.templateGrid}>
              <button className={`${styles.cardButton} ${styles.blankCard}`} onClick={onCreateBlank} type="button">
                <div className={styles.cardTop}>
                  <div className={styles.cardIcon} style={{ background: "var(--bg-elevated)", color: "var(--text-muted)", border: "1px solid var(--border)" }}>
                    <Plus size={22} />
                  </div>
                </div>
                <div className={styles.cardBody}>
                  <div className={styles.cardTitle}>Blank workflow</div>
                  <p className={styles.cardText}>Start from zero and add the exact nodes you need piece by piece.</p>
                </div>
                <div className={styles.cardFooter}>
                  <span className={styles.cardFooterLink}>
                    Start blank <ArrowRight size={14} />
                  </span>
                </div>
              </button>

              {filteredTemplates.map((t) => (
                <TemplateCard key={t.name} template={t} onClick={() => onLoadTemplate(t.config)} />
              ))}
            </div>

            {filteredTemplates.length === 0 && (
              <div className={styles.emptyState}>
                No templates found matching &ldquo;{search}&rdquo;.
              </div>
            )}
          </section>

          {/*  Saved workflows  */}
          {saved.length > 0 && (
            <section className={styles.section}>
              <div className={styles.sectionHeader}>
                <div>
                  <h2 className={styles.sectionTitle}>Saved workflows</h2>
                  <p className={styles.sectionDescription}>Pick up right where you left off.</p>
                </div>
              </div>
              <div className={styles.savedGrid}>
                {saved.map((pipeline) => (
                  <SavedWorkflowCard key={pipeline.id} pipeline={pipeline} onClick={() => onLoadPipeline(pipeline)} />
                ))}
              </div>
            </section>
          )}
        </div>
      </main>
    </div>
  );
}

export { TEMPLATES };
export type { Template };