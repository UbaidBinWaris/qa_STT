/**
 * Shared contracts. One definition, used by portal, admin, backend — and mirrored
 * by the Python worker so a field rename cannot silently break the pipeline.
 */

/** Lifecycle of a recording, owned by NestJS. Python only reports progress. */
export type CallStatus = "queued" | "processing" | "completed" | "failed";

/** Pipeline stages, in order. Python reports these as it advances. */
export type CallStage =
  | "converting"
  | "transcribing"
  | "diarizing"
  | "recovering"
  | "verifying"
  | "aligning"
  | "analyzing"
  | "saving"
  | "done";

/**
 * Python has no database access by design — it posts progress here and NestJS
 * is the only writer. That keeps one owner for the data and means the GPU box
 * can be restarted, moved or replaced without touching Postgres.
 */
export interface WorkerProgressReport {
  callId: string;
  status: CallStatus;
  stage: CallStage;
  progress: number; // 0-100
  error?: string | null;
}

/** How confident we are in the transcript itself, not in the QA verdict. */
export interface ReliabilitySummary {
  available: boolean;
  score: number | null; // 0-100
  meanConfidence?: number;
  flagged: number; // words a reviewer should check
  total: number;
  conflicts?: number; // the two decoders disagreed here
  crosstalkSeconds?: number; // speech that could not be transcribed at all
  recoveredWords?: number; // speech the first pass dropped, recovered on retry
}

export interface Word {
  word: string;
  start: number;
  end: number;
  confidence: number | null;
  uncertain: boolean;
  conflict?: boolean;
  recovered?: boolean;
  risk: string[]; // negation | number | money | date | contact | compliance | proper-noun
}

export interface Segment {
  id: number;
  role: string; // Agent | Customer
  start: number;
  end: number;
  text: string;
  confidence: number | null;
  uncertain: boolean;
  crosstalk: boolean;
  prosody: {
    f0Hz: number | null;
    loudnessDb: number;
    arousal: number | null;
    tone: "flat" | "neutral" | "elevated";
  } | null;
  words: Word[];
}

/** Every finding must prove itself: quote, timestamp and speaker are verified. */
export interface Finding {
  rule: string;
  severity: "low" | "medium" | "high";
  quote: string;
  timestamp: number;
  verified?: {
    segmentStart: number;
    speaker: string;
    transcriptUncertain?: boolean;
  };
}

export interface Call {
  id: string;
  orgId: string;
  filename: string;
  durationSeconds: number | null;
  status: CallStatus;
  stage: CallStage | null;
  progress: number;
  error: string | null;
  score: number | null;
  reliability: ReliabilitySummary | null;
  createdAt: string;
}
