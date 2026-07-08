"use client";

import { useMemo, type ReactNode } from "react";
import { Activity, Zap, BarChart2, Clock } from "lucide-react";
import {
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  BarChart,
  Bar,
  ScatterChart,
  Scatter,
  ZAxis,
  ReferenceLine,
  Legend,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts";

import Link from "next/link";

import {
  useAnalytics,
  useCalibration,
  useActiveLearningCandidates,
} from "@/hooks/useAnalytics";
import { useEmailQueue } from "@/hooks/useEmailQueue";
import { Badge, StatCard, EmptyState, ErrorBanner, LoadingSpinner } from "@/components/ui";
import { formatIntentLabel } from "@/lib/format";
import type { ActiveLearningCandidate, CalibrationBucket } from "@/types";

// Chart-only hex palette. recharts writes these into SVG fill/stroke attributes,
// which do NOT resolve CSS variables — so these mirror the globals.css tokens.
const C = {
  green: "#10b981",
  yellow: "#f59e0b",
  red: "#ef4444",
  accent: "#6366f1",
  faq: "#6366f1",
  review: "#f59e0b",
  axis: "#8b91a8",
  grid: "#2a2f45",
  surface: "#1a1d27",
  text: "#f0f2f8",
};

const TOOLTIP_STYLE = {
  contentStyle: {
    backgroundColor: C.surface,
    border: `1px solid ${C.grid}`,
    borderRadius: 8,
    color: C.text,
    fontSize: 12,
  },
  labelStyle: { color: C.axis },
  itemStyle: { color: C.text },
} as const;

export default function AnalyticsPage() {
  const { summary, isLoading: aLoading, isError: aError } = useAnalytics();
  const { emails, isLoading: eLoading, isError: eError } = useEmailQueue();
  const { calibration } = useCalibration();
  const { candidates } = useActiveLearningCandidates();

  const isError = aError || eError;
  const isLoading = (aLoading || eLoading) && !summary;

  const total = summary?.total_emails ?? 0;
  const faq = summary?.faq_lane_count ?? 0;
  const human = summary?.human_review_count ?? 0;

  const laneData = useMemo(
    () => [
      { name: "FAQ", value: faq, color: C.faq },
      { name: "Human Review", value: human, color: C.review },
    ],
    [faq, human]
  );

  const intentData = useMemo(() => {
    const dist = summary?.intent_distribution ?? {};
    return Object.entries(dist)
      .map(([k, v]) => ({ label: formatIntentLabel(k), count: v }))
      .sort((a, b) => b.count - a.count);
  }, [summary]);

  const confidenceData = useMemo(() => {
    const bands = [
      { band: "0–0.5", color: C.red, count: 0, test: (c: number) => c < 0.5 },
      { band: "0.5–0.6", color: C.yellow, count: 0, test: (c: number) => c < 0.6 },
      { band: "0.6–0.7", color: C.yellow, count: 0, test: (c: number) => c < 0.7 },
      { band: "0.7–0.8", color: C.yellow, count: 0, test: (c: number) => c < 0.8 },
      { band: "0.8–0.9", color: C.green, count: 0, test: (c: number) => c < 0.9 },
      { band: "0.9–1.0", color: C.green, count: 0, test: () => true },
    ];
    for (const email of emails) {
      const c = email.classification?.confidence;
      if (typeof c !== "number") continue;
      const bucket = bands.find((b) => b.test(c)) ?? bands[bands.length - 1];
      bucket.count += 1;
    }
    return bands;
  }, [emails]);

  return (
    <div className="mx-auto w-full max-w-6xl px-8 py-10">
      {/* SECTION A — Header */}
      <header className="mb-8 flex flex-col gap-1">
        <h1
          className="text-2xl font-semibold tracking-tight"
          style={{ color: "var(--text-primary)" }}
        >
          Analytics
        </h1>
        <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
          Pipeline performance and routing breakdown
        </p>
      </header>

      {isError && (
        <ErrorBanner className="mb-6" message="Couldn't load analytics data." />
      )}

      {isLoading ? (
        <div className="flex items-center justify-center py-32">
          <LoadingSpinner size="lg" />
        </div>
      ) : (
        <div className="flex flex-col gap-6">
          {/* SECTION B — KPI row */}
          <section className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <StatCard
              label="Total Processed"
              value={total}
              icon={<Activity className="h-4 w-4" />}
            />
            <StatCard
              label="Auto-Reply Rate"
              value={`${total ? ((faq / total) * 100).toFixed(1) : "0.0"}%`}
              icon={<Zap className="h-4 w-4" />}
              accent="var(--faq-color)"
            />
            <StatCard
              label="Avg Confidence"
              value={`${((summary?.avg_confidence ?? 0) * 100).toFixed(1)}%`}
              icon={<BarChart2 className="h-4 w-4" />}
            />
            <StatCard
              label="Pending Review"
              value={summary?.pending_count ?? 0}
              icon={<Clock className="h-4 w-4" />}
              accent="var(--review-color)"
            />
          </section>

          {/* SECTION C + E */}
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            {/* C — Routing split donut */}
            <Panel title="Routing Split">
              {total === 0 ? (
                <ChartEmpty />
              ) : (
                <>
                  <div className="h-[240px]">
                    <ResponsiveContainer width="100%" height="100%">
                      <PieChart>
                        <Pie
                          data={laneData}
                          dataKey="value"
                          nameKey="name"
                          cx="50%"
                          cy="50%"
                          innerRadius={60}
                          outerRadius={90}
                          paddingAngle={2}
                          stroke="none"
                        >
                          {laneData.map((d) => (
                            <Cell key={d.name} fill={d.color} />
                          ))}
                        </Pie>
                        <Tooltip {...TOOLTIP_STYLE} />
                      </PieChart>
                    </ResponsiveContainer>
                  </div>
                  {/* Custom legend */}
                  <div className="mt-2 flex flex-col gap-2">
                    {laneData.map((d) => (
                      <div
                        key={d.name}
                        className="flex items-center gap-2 text-sm"
                      >
                        <span
                          className="h-2.5 w-2.5 rounded-full"
                          style={{ backgroundColor: d.color }}
                        />
                        <span style={{ color: "var(--text-secondary)" }}>
                          {d.name}
                        </span>
                        <span
                          className="ml-auto tabular-nums"
                          style={{ color: "var(--text-primary)" }}
                        >
                          {d.value}
                        </span>
                        <span
                          className="w-12 text-right tabular-nums"
                          style={{ color: "var(--text-muted)" }}
                        >
                          {total ? ((d.value / total) * 100).toFixed(0) : 0}%
                        </span>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </Panel>

            {/* E — Confidence distribution */}
            <Panel title="Confidence Distribution">
              {emails.length === 0 ? (
                <ChartEmpty />
              ) : (
                <div className="h-[280px]">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart
                      data={confidenceData}
                      margin={{ top: 8, right: 8, bottom: 0, left: -16 }}
                    >
                      <CartesianGrid
                        strokeDasharray="3 3"
                        stroke={C.grid}
                        vertical={false}
                      />
                      <XAxis
                        dataKey="band"
                        tick={{ fill: C.axis, fontSize: 11 }}
                        axisLine={{ stroke: C.grid }}
                        tickLine={false}
                      />
                      <YAxis
                        allowDecimals={false}
                        tick={{ fill: C.axis, fontSize: 12 }}
                        axisLine={false}
                        tickLine={false}
                      />
                      <Tooltip cursor={{ fill: "rgba(255,255,255,0.04)" }} {...TOOLTIP_STYLE} />
                      <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                        {confidenceData.map((d) => (
                          <Cell key={d.band} fill={d.color} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}
            </Panel>
          </div>

          {/* SECTION D — Intent breakdown */}
          <Panel title="Emails by Intent">
            {intentData.length === 0 ? (
              <ChartEmpty />
            ) : (
              <div className="h-[340px]">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={intentData}
                    layout="vertical"
                    margin={{ top: 4, right: 16, bottom: 4, left: 8 }}
                  >
                    <CartesianGrid
                      strokeDasharray="3 3"
                      stroke={C.grid}
                      horizontal={false}
                    />
                    <XAxis
                      type="number"
                      allowDecimals={false}
                      tick={{ fill: C.axis, fontSize: 12 }}
                      axisLine={{ stroke: C.grid }}
                      tickLine={false}
                    />
                    <YAxis
                      type="category"
                      dataKey="label"
                      width={150}
                      tick={{ fill: C.axis, fontSize: 12 }}
                      axisLine={false}
                      tickLine={false}
                    />
                    <Tooltip cursor={{ fill: "rgba(255,255,255,0.04)" }} {...TOOLTIP_STYLE} />
                    <Bar dataKey="count" fill={C.accent} radius={[0, 4, 4, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </Panel>

          {/* SECTION F — Calibration reliability diagram (Phase 5B/5E) */}
          <Panel title="Classifier Calibration Reliability">
            <CalibrationDiagram calibration={calibration} />
          </Panel>

          {/* SECTION G — Active-learning candidates (Phase 5G) */}
          <Panel title="Active-Learning Candidates">
            <ActiveLearningCandidates candidates={candidates} />
          </Panel>
        </div>
      )}
    </div>
  );
}

function ActiveLearningCandidates({
  candidates,
}: {
  candidates: ActiveLearningCandidate[];
}) {
  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
        Emails a chair rescued near the confidence threshold, or substantially
        rewrote before sending — surfaced here for a future human labeling pass.
        This is a review list only; no retraining is triggered.
      </p>

      {candidates.length === 0 ? (
        <EmptyState
          icon={<Activity className="h-5 w-5" />}
          title="No candidates flagged yet"
          description="Emails get flagged as chairs approve near-threshold cases or edit drafts. None so far."
        />
      ) : (
        <ul className="flex flex-col gap-2">
          {candidates.map((c) => (
            <li
              key={c.email_id}
              className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border p-3"
              style={{
                backgroundColor: "var(--surface-raised)",
                borderColor: "var(--border-subtle)",
              }}
            >
              <span
                className="text-sm font-medium"
                style={{ color: "var(--text-primary)" }}
              >
                {c.subject || "(no subject)"}
              </span>
              <span className="text-xs" style={{ color: "var(--text-muted)" }}>
                #{c.email_id}
              </span>

              {c.low_confidence && (
                <Badge variant="warning" size="sm">
                  low confidence
                  {c.low_confidence.confidence_used != null &&
                    ` · ${c.low_confidence.confidence_used.toFixed(2)} < ${c.low_confidence.threshold.toFixed(2)}`}
                </Badge>
              )}
              {c.meaningful_edit && (
                <Badge variant="faq" size="sm">
                  edited · {(c.meaningful_edit.change_ratio * 100).toFixed(0)}% changed
                </Badge>
              )}

              <Link
                href="/queue"
                className="ml-auto text-xs font-medium transition-opacity hover:opacity-80"
                style={{ color: "var(--accent)" }}
              >
                View in queue ›
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function CalibrationTooltip({ active, payload }: {
  active?: boolean;
  payload?: { payload: CalibrationBucket }[];
}) {
  if (!active || !payload?.length) return null;
  const b = payload[0].payload;
  return (
    <div
      style={{
        backgroundColor: C.surface,
        border: `1px solid ${C.grid}`,
        borderRadius: 8,
        color: C.text,
        fontSize: 12,
        padding: "8px 10px",
      }}
    >
      <div style={{ color: C.axis }}>bucket {b.bucket}</div>
      <div>mean confidence: {b.mean_confidence.toFixed(3)}</div>
      <div>actual accuracy: {b.accuracy.toFixed(3)}</div>
      <div>gap: {b.gap >= 0 ? "+" : ""}{b.gap.toFixed(3)}</div>
      <div style={{ color: C.axis }}>n = {b.n} email{b.n === 1 ? "" : "s"}</div>
    </div>
  );
}

function CalibrationDiagram({
  calibration,
}: {
  calibration:
    | {
        eval_set_size: number;
        calibrated_available: boolean;
        raw: CalibrationBucket[];
        calibrated: CalibrationBucket[] | null;
        metrics: {
          brier_raw: number;
          ece_raw: number;
          brier_calibrated?: number;
          ece_calibrated?: number;
        };
        caveat: string;
      }
    | undefined;
}) {
  if (!calibration) {
    return (
      <div className="flex items-center justify-center py-16">
        <LoadingSpinner size="md" />
      </div>
    );
  }
  if (calibration.raw.length === 0) {
    return <ChartEmpty />;
  }

  const { metrics } = calibration;

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
        Each point is a confidence decile: x = the classifier&apos;s mean confidence,
        y = the emails&apos; actual accuracy. Points on the dashed diagonal are
        perfectly calibrated; points above it mean the classifier is
        under-confident (the Phase 5B finding). Point size reflects the bucket
        sample size (n).
      </p>

      <div className="h-[340px]">
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 8, right: 16, bottom: 24, left: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={C.grid} />
            <XAxis
              type="number"
              dataKey="mean_confidence"
              name="Mean confidence"
              domain={[0, 1]}
              ticks={[0, 0.2, 0.4, 0.6, 0.8, 1]}
              tick={{ fill: C.axis, fontSize: 11 }}
              axisLine={{ stroke: C.grid }}
              tickLine={false}
              label={{ value: "Mean confidence", position: "bottom", fill: C.axis, fontSize: 11 }}
            />
            <YAxis
              type="number"
              dataKey="accuracy"
              name="Accuracy"
              domain={[0, 1]}
              ticks={[0, 0.2, 0.4, 0.6, 0.8, 1]}
              tick={{ fill: C.axis, fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              label={{ value: "Accuracy", angle: -90, position: "insideLeft", fill: C.axis, fontSize: 11 }}
            />
            <ZAxis type="number" dataKey="n" range={[50, 320]} name="n" />
            {/* Perfect-calibration reference (y = x). */}
            <ReferenceLine
              segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]}
              stroke={C.axis}
              strokeDasharray="4 4"
            />
            <Tooltip cursor={{ strokeDasharray: "3 3" }} content={<CalibrationTooltip />} />
            <Legend wrapperStyle={{ fontSize: 12, color: C.text }} />
            <Scatter name="Raw confidence" data={calibration.raw} fill={C.review} fillOpacity={0.85} />
            {calibration.calibrated_available && calibration.calibrated && (
              <Scatter
                name="Calibrated"
                data={calibration.calibrated}
                fill={C.green}
                fillOpacity={0.85}
              />
            )}
          </ScatterChart>
        </ResponsiveContainer>
      </div>

      {/* Metrics row */}
      <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs" style={{ color: "var(--text-secondary)" }}>
        <span>
          ECE: <span style={{ color: C.review }}>{metrics.ece_raw.toFixed(3)} raw</span>
          {metrics.ece_calibrated != null && (
            <> → <span style={{ color: C.green }}>{metrics.ece_calibrated.toFixed(3)} calibrated</span></>
          )}
        </span>
        <span>
          Brier: <span style={{ color: C.review }}>{metrics.brier_raw.toFixed(3)} raw</span>
          {metrics.brier_calibrated != null && (
            <> → <span style={{ color: C.green }}>{metrics.brier_calibrated.toFixed(3)} calibrated</span></>
          )}
        </span>
      </div>

      {!calibration.calibrated_available && (
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>
          Calibrated series unavailable — no calibrator has been fitted yet (run
          the calibration training step to populate it).
        </p>
      )}

      {/* Visible in-sample caveat (not buried in a tooltip). */}
      <p
        className="rounded-md px-3 py-2 text-xs"
        style={{
          color: "var(--text-secondary)",
          backgroundColor: "rgba(245,158,11,0.08)",
          border: "1px solid rgba(245,158,11,0.25)",
        }}
      >
        {calibration.caveat}
      </p>
    </div>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section
      className="rounded-xl border p-5"
      style={{ backgroundColor: "var(--surface)", borderColor: "var(--border)" }}
    >
      <h2
        className="mb-4 text-sm font-semibold"
        style={{ color: "var(--text-primary)" }}
      >
        {title}
      </h2>
      {children}
    </section>
  );
}

function ChartEmpty() {
  return (
    <EmptyState
      icon={<BarChart2 className="h-5 w-5" />}
      title="No data yet"
      description="Charts populate once the pipeline has processed emails."
    />
  );
}
