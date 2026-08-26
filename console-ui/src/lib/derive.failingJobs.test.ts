import { describe, expect, it } from 'vitest';
import { failingJobs, firstErrorLine, type ScheduleJobLike } from './derive';

const job = (over: Partial<ScheduleJobLike>): ScheduleJobLike => ({
  id: 'j',
  enabled: true,
  last_status: 'ok',
  last_run: '2026-08-25T09:00:00',
  ...over,
});

describe('firstErrorLine', () => {
  it('picks the diagnosis, not the first line of progress chatter', () => {
    // Verbatim from the live vault: a crystallize failure whose tail OPENS
    // with progress output and whose cause is the last line. Taking line one
    // showed the operator "embedding 1 pack(s) with Xenova/…".
    const real = [
      'crystal-synth: embedding 1 pack(s) with Xenova/paraphrase-multilingual-MiniLM-L12-v2 for llm cluster mode',
      'error: gate: crystal-synth: strength verdicts incomplete for wave 3 — missing=["l3-x"]',
    ].join('\n');
    expect(firstErrorLine(real)).toBe(
      'error: gate: crystal-synth: strength verdicts incomplete for wave 3 — missing=["l3-x"]',
    );
  });

  it('takes the last line, so a recovered error earlier in the run does not win', () => {
    expect(firstErrorLine('error: transient, retrying\nworking\nerror: fatal, giving up')).toBe(
      'error: fatal, giving up',
    );
  });

  it('keeps a cause that carries no error keyword', () => {
    // Keyword matching returned "Error: job failed" here and threw the actual
    // cause away — neither of the lines below would match an error vocabulary.
    expect(
      firstErrorLine('Error: job failed\nCaused by:\nPermission denied (os error 13)'),
    ).toBe('Permission denied (os error 13)');
  });

  it('falls back to the newest line when nothing reads as an error', () => {
    // The newest thing a dying run said beats the oldest.
    expect(firstErrorLine('step one\nstep two\nstep three')).toBe('step three');
  });

  it('bounds a long line so it cannot blow out a one-line banner', () => {
    const long = 'x'.repeat(400);
    const out = firstErrorLine(long)!;
    expect(out).toHaveLength(160);
    expect(out.endsWith('…')).toBe(true);
  });

  it('treats empty and whitespace-only tails as no reason', () => {
    for (const t of [null, undefined, '', '   \n\n  ']) {
      expect(firstErrorLine(t)).toBeNull();
    }
  });
});

describe('failingJobs', () => {
  it('surfaces a job whose last run failed', () => {
    // The live case this exists for: crystallize had been in `error` for two
    // days while every page showed a green banner, because the banner only
    // ever looked at `daily`.
    const out = failingJobs([
      job({ id: 'crystallize', last_status: 'error', consecutive_failures: 1, last_error: 'error: gate: boom' }),
      job({ id: 'themes' }),
    ]);
    expect(out).toEqual([
      {
        id: 'crystallize',
        streak: 1,
        lastRun: '2026-08-25T09:00:00',
        reason: 'error: gate: boom',
      },
    ]);
  });

  it('excludes daily, which the banner already reports from the heartbeat', () => {
    // Listing it here too would double-report the same run, with a poorer
    // status than the heartbeat carries.
    const out = failingJobs([job({ id: 'daily', last_status: 'error' })]);
    expect(out).toEqual([]);
  });

  it('ignores disabled jobs', () => {
    // A paused job is not a failure to act on — it is a decision.
    const out = failingJobs([
      job({ id: 'x', enabled: false, last_status: 'error' }),
    ]);
    expect(out).toEqual([]);
  });

  it('ignores ok and seeded rows', () => {
    const out = failingJobs([
      job({ id: 'a', last_status: 'ok' }),
      job({ id: 'b', last_status: 'seeded' }),
    ]);
    expect(out).toEqual([]);
  });

  it('orders by streak so the longest-running failure is read first', () => {
    const out = failingJobs([
      job({ id: 'b', last_status: 'error', consecutive_failures: 1 }),
      job({ id: 'a', last_status: 'error', consecutive_failures: 5 }),
    ]);
    expect(out.map((j) => j.id)).toEqual(['a', 'b']);
  });

  it('defaults a missing counter to a streak of 1 rather than 0', () => {
    // An older server omits the field; reporting "0 consecutive failures"
    // next to a failed job reads as "not really failing".
    const out = failingJobs([job({ id: 'a', last_status: 'error' })]);
    expect(out[0].streak).toBe(1);
  });

  it('tolerates a null schedule', () => {
    expect(failingJobs(null)).toEqual([]);
    expect(failingJobs(undefined)).toEqual([]);
  });
});
