import { useEffect, useMemo, useState } from "react";
import { Activity, CheckCircle2, ChevronRight, Circle, GitBranch } from "lucide-react";

import type { PlanTreeNode } from "../../types/api";

interface PlanSidebarProps {
  completedStages: number;
  totalStages: number;
  progress: number;
  masteryScore: number;
  tree: PlanTreeNode | null;
  planCompleted: boolean;
}

export function PlanSidebar({
  completedStages,
  totalStages,
  progress,
  masteryScore,
  tree,
  planCompleted,
}: PlanSidebarProps) {
  const defaultExpandedKeys = useMemo(() => collectCurrentBranchKeys(tree), [tree]);
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(() => new Set(defaultExpandedKeys));

  useEffect(() => {
    setExpandedKeys((previous) => {
      const next = new Set(defaultExpandedKeys);
      for (const key of previous) {
        next.add(key);
      }
      return next;
    });
  }, [defaultExpandedKeys]);

  return (
    <aside className="hidden w-96 flex-col border-l border-slate-200 bg-white lg:flex">
      <div className="flex-1 overflow-y-auto p-6">
        <h3 className="mb-6 flex items-center gap-2 text-sm font-bold uppercase tracking-wider text-slate-900">
          <Activity className="h-4 w-4 text-teal-600" />
          Study Plan Progress
        </h3>

        <div className="mb-6 rounded-2xl border border-slate-200 bg-slate-50 p-5">
          <div className="mb-3 flex items-end justify-between">
            <span className="text-sm font-semibold text-slate-700">Completed</span>
            <span className="text-xl font-bold text-slate-900">
              {completedStages}
              <span className="text-sm font-medium text-slate-500">/{totalStages || "-"}</span>
            </span>
          </div>
          <div className="mb-2 h-2.5 w-full overflow-hidden rounded-full bg-slate-200">
            <div className="h-2.5 rounded-full bg-teal-500" style={{ width: `${progress}%` }} />
          </div>
          <div className="flex items-center justify-between text-xs font-medium text-slate-500">
            <span>{progress}% done</span>
            <span>Subject score {Math.round(masteryScore * 100)}%</span>
          </div>
        </div>

        {tree ? (
          <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
              <GitBranch className="h-4 w-4 text-teal-600" />
              {tree.title}
            </div>
            <div className="mb-4 text-xs text-slate-500">
              {tree.completed_leaf_count}/{tree.total_leaf_count} completed ·{" "}
              {Math.round(tree.mastery_score * 100)}% mastery
            </div>
            <div className="space-y-1">
              {tree.children.map((child) => (
                <PlanTreeBranch
                  key={`${child.node_type}:${child.title}:${child.stage_index ?? child.breadcrumb.join("/")}`}
                  node={child}
                  depth={0}
                  expandedKeys={expandedKeys}
                  onToggle={(key) =>
                    setExpandedKeys((previous) => {
                      const next = new Set(previous);
                      if (next.has(key)) {
                        next.delete(key);
                      } else {
                        next.add(key);
                      }
                      return next;
                    })
                  }
                />
              ))}
            </div>
          </section>
        ) : (
          <section className="rounded-2xl border border-dashed border-slate-300 bg-white/80 p-4 text-sm text-slate-500">
            Start a session to load the study plan tree.
          </section>
        )}

        <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-xs text-slate-500">
          {planCompleted
            ? "Plan completed. Review the tree and revisit weaker branches."
            : "The current branch stays expanded. Other branches remain compact with progress summaries."}
        </div>
      </div>
    </aside>
  );
}

function PlanTreeBranch({
  node,
  depth,
  expandedKeys,
  onToggle,
}: {
  node: PlanTreeNode;
  depth: number;
  expandedKeys: Set<string>;
  onToggle: (key: string) => void;
}) {
  const isStage = node.node_type === "stage";
  const nodeKey = branchKey(node);
  const isExpanded = isStage ? false : expandedKeys.has(nodeKey);
  const isCompleted = Boolean(node.completed);
  const ratio = `${node.completed_leaf_count}/${node.total_leaf_count || 0}`;
  const masteryPercent = Math.round((node.mastery_score || 0) * 100);
  const clickable = !isStage;

  return (
    <div>
      <button
        type="button"
        onClick={clickable ? () => onToggle(nodeKey) : undefined}
        className={`flex items-start gap-3 rounded-xl px-3 py-2 ${
          node.is_current_stage
            ? "border border-teal-200 bg-teal-50"
            : node.is_current_branch
              ? "bg-slate-50"
              : ""
        } ${clickable ? "w-full text-left transition-colors hover:bg-slate-50" : "w-full text-left"}`}
        style={{ marginLeft: `${depth * 12}px` }}
        aria-expanded={clickable ? isExpanded : undefined}
      >
        <div className="mt-0.5 flex items-center gap-1 text-slate-400">
          {clickable ? (
            <ChevronRight
              className={`h-4 w-4 transition-transform ${isExpanded ? "rotate-90" : ""}`}
            />
          ) : null}
          {isCompleted ? (
            <CheckCircle2 className="h-4 w-4 text-teal-600" />
          ) : node.is_current_stage ? (
            <div className="h-4 w-4 rounded-full border-2 border-teal-600 bg-white" />
          ) : (
            <Circle className="h-4 w-4" />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <p
              className={`truncate text-sm ${
                node.is_current_stage ? "font-semibold text-slate-900" : "text-slate-700"
              }`}
            >
              {node.title}
            </p>
            <span className="shrink-0 text-[11px] font-medium text-slate-500">
              {ratio} · {masteryPercent}%
            </span>
          </div>
        </div>
      </button>

      {!isStage && isExpanded ? (
        <div className="mt-1 space-y-1">
          {node.children.map((child) => (
            <PlanTreeBranch
              key={`${child.node_type}:${child.title}:${child.stage_index ?? child.breadcrumb.join("/")}`}
              node={child}
              depth={depth + 1}
              expandedKeys={expandedKeys}
              onToggle={onToggle}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function branchKey(node: PlanTreeNode): string {
  return `${node.node_type}:${node.breadcrumb.join(" / ")}:${node.stage_index ?? ""}`;
}

function collectCurrentBranchKeys(tree: PlanTreeNode | null): string[] {
  if (!tree) {
    return [];
  }

  const keys: string[] = [];

  const walk = (node: PlanTreeNode) => {
    if (node.node_type !== "stage" && (node.is_current_branch || node.node_type === "book")) {
      keys.push(branchKey(node));
      node.children.forEach(walk);
    }
  };

  walk(tree);
  return keys;
}
