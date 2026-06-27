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
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts";

import { useAnalytics } from "@/hooks/useAnalytics";
import { useEmailQueue } from "@/hooks/useEmailQueue";
import { StatCard, EmptyState, ErrorBanner, LoadingSpinner } from "@/components/ui";
import { formatIntentLabel } from "@/lib/format";

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
        </div>
      )}
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
