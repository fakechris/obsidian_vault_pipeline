/** Theme detail `/knowledge/theme/:theme` — answers US3/US4 (design §3.3).
 *
 * Two-column layout so the theme graph stays on the first screen:
 * left = Topic overview (when present) + claims list (durable first,
 * caveated marked); right rail = KnowledgeGraph at theme scope (sticky).
 * Every claim card carries id=<claim_id> so /knowledge/theme/:t#<claim_id>
 * scrolls to and highlights the card — the same anchor pattern the source
 * page uses for unit line anchors. Cited sources link to /library/:sha;
 * legacy case ids whose pack has no source sha render as plain text
 * (handoff note 5: never navigate to a 404). */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useParams } from 'react-router-dom';
import KnowledgeGraph from '../components/KnowledgeGraph';
import { ClaimPill, EmptyState, ModelGate } from '../components/ui';
import { useI18n } from '../i18n';
import { fetchThemePages } from '../lib/api';
import {
  isMiscTheme,
  parsePageBody,
  sourcesByCase,
  themeClaims,
  themeFromRoute,
} from '../lib/derive';
import type {
  ClaimRow,
  IndexModel,
  SourceRow,
  ThemePagesResponse,
} from '../lib/types';
import { useModel } from '../model';

function TopicOverview({ theme }: { theme: string }) {
  const { t, lang } = useI18n();
  const [themePages, setThemePages] = useState<ThemePagesResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchThemePages().then(
      (response) => {
        if (!cancelled) setThemePages(response);
      },
      () => {
        // Optional enhancement: fetch failures leave the original page intact.
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  const page = themePages?.pages.find((candidate) => candidate.label === theme);
  const overview = useMemo(() => {
    if (!page || !themePages) return null;
    // Prefer rebuildable sections_zh when UI lang is Chinese.
    const sectionSrc =
      lang === 'zh' && page.sections_zh && page.sections_zh.length > 0
        ? page.sections_zh
        : page.sections;
    const sections = sectionSrc.map((section) => ({
      ...section,
      paragraphs: parsePageBody(section.body),
    }));
    const citationNumberByKey = new Map<string, number>();
    for (const section of sections) {
      for (const paragraph of section.paragraphs) {
        for (const token of paragraph) {
          if (
            token.kind === 'cite' &&
            themePages.claims[token.key] &&
            !citationNumberByKey.has(token.key)
          ) {
            citationNumberByKey.set(token.key, citationNumberByKey.size + 1);
          }
        }
      }
    }
    // claim_ids can collide across runs while claim_keys stay unique. The
    // card anchors below are keyed by claim_id, so a chip pointing at a
    // duplicated id could scroll to the wrong card — such chips render as
    // plain (tooltip-only) markers instead of links.
    const idCounts = new Map<string, number>();
    for (const info of Object.values(themePages.claims)) {
      idCounts.set(info.claim_id, (idCounts.get(info.claim_id) ?? 0) + 1);
    }
    const ambiguousIds = new Set(
      [...idCounts.entries()].filter(([, n]) => n > 1).map(([id]) => id),
    );
    return { sections, citationNumberByKey, ambiguousIds };
  }, [page, themePages, lang]);

  if (!page || !themePages || !overview) return null;

  const displayLabel =
    lang === 'zh' && page.label_zh?.trim() ? page.label_zh : page.label;

  return (
    <div className="card topic-overview">
      <h2>{t('theme.topicOverview')}</h2>
      <div className="claim-meta topic-overview-caption">
        {displayLabel} · {t('theme.topicOverviewCaption', { n: page.claim_count })}
      </div>
      {overview.sections.map((section, sectionIndex) => (
        <section
          className="topic-overview-section"
          key={`${section.heading}-${sectionIndex}`}
        >
          <h3>{section.heading}</h3>
          {section.paragraphs.map((paragraph, paragraphIndex) => (
            <p key={paragraphIndex}>
              {paragraph.map((token, tokenIndex) => {
                if (token.kind === 'text') {
                  return <span key={tokenIndex}>{token.text}</span>;
                }
                const claim = themePages.claims[token.key];
                const number = overview.citationNumberByKey.get(token.key);
                if (!claim || number == null) return null;
                const tip =
                  lang === 'zh' && claim.claim_zh?.trim()
                    ? claim.claim_zh
                    : claim.claim;
                if (overview.ambiguousIds.has(claim.claim_id)) {
                  return (
                    <span
                      className="topic-cite"
                      key={`${token.key}-${tokenIndex}`}
                      title={tip}
                    >
                      [{number}]
                    </span>
                  );
                }
                return (
                  <Link
                    className="topic-cite"
                    key={`${token.key}-${tokenIndex}`}
                    title={tip}
                    to={{ hash: `#${encodeURIComponent(claim.claim_id)}` }}
                  >
                    [{number}]
                  </Link>
                );
              })}
            </p>
          ))}
        </section>
      ))}
    </div>
  );
}

function ClaimSources({
  claim,
  byCase,
}: {
  claim: ClaimRow;
  byCase: Map<string, SourceRow>;
}) {
  const { t } = useI18n();
  if (claim.sources.length === 0) return null;
  return (
    <div className="claim-meta">
      {t('theme.citedSources')}{' '}
      {claim.sources.map((caseId, i) => {
        const src = byCase.get(caseId);
        return (
          <span key={caseId}>
            {i > 0 && ' · '}
            {src ? (
              <Link to={`/library/${src.sha256}`}>
                {src.title ?? caseId}
              </Link>
            ) : (
              <span title={t('theme.legacySource')}>{caseId}</span>
            )}
          </span>
        );
      })}
    </div>
  );
}

function ClaimCard({
  claim,
  byCase,
  highlighted,
  claimZh,
}: {
  claim: ClaimRow;
  byCase: Map<string, SourceRow>;
  highlighted: boolean;
  /** Optional zh from theme-pages / claims_zh projection (by claim_key). */
  claimZh?: string | null;
}) {
  const { t, lang } = useI18n();
  // Content language follows UI lang (top-bar EN/中): zh prefers claim_zh
  // projection, falls back to English authority when missing/stale.
  const zh = (claimZh ?? claim.claim_zh ?? '').trim();
  const hasZh = zh.length > 0;
  const text = lang === 'zh' && hasZh ? zh : claim.claim;
  return (
    <div
      className={`card claim-card${highlighted ? ' claim-hit' : ''}`}
      id={claim.claim_id}
    >
      <div className="claim-top">
        {(claim.status === 'durable' || claim.status === 'caveated') && (
          <ClaimPill status={claim.status} />
        )}
        {claim.strength && (
          <span className="tiny muted">
            {t('theme.strength')} {claim.strength}
          </span>
        )}
        {lang === 'zh' && !hasZh && (
          <span className="tiny muted" title={t('theme.claimZhMissingTip')}>
            {t('theme.claimEnOnly')}
          </span>
        )}
        {/* Scroll via onClick rather than a native `#id` href: under the
            static site's HashRouter an `#id` href would replace the route hash
            and navigate away instead of scrolling. Works in both router modes. */}
        <button
          type="button"
          className="claim-anchor mono tiny"
          onClick={() =>
            document
              .getElementById(claim.claim_id)
              ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
          }
        >
          #{claim.claim_id}
        </button>
      </div>
      <div className="claim-text">{text}</div>
      <ClaimSources claim={claim} byCase={byCase} />
    </div>
  );
}

function ThemeBody({ model, theme }: { model: IndexModel; theme: string }) {
  const { t, lang, setLang } = useI18n();
  const location = useLocation();
  const claims = useMemo(() => themeClaims(model.claims, theme), [model, theme]);
  const byCase = useMemo(() => sourcesByCase(model), [model]);
  // theme-pages payload carries claim_zh keyed by claim_key when
  // .ovp/crystal/claims_zh.json exists — reuse for cards (index model may not
  // splice claim_zh on every claim row yet).
  const [themePages, setThemePages] = useState<ThemePagesResponse | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetchThemePages().then(
      (r) => {
        if (!cancelled) setThemePages(r);
      },
      () => {
        /* optional */
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);
  const zhByKey = useMemo(() => {
    const m = new Map<string, string>();
    if (!themePages?.claims) return m;
    for (const [key, info] of Object.entries(themePages.claims)) {
      const z = info.claim_zh?.trim();
      if (z) m.set(key, z);
    }
    return m;
  }, [themePages]);
  const zhCount = useMemo(() => {
    let n = 0;
    for (const c of claims) {
      const z =
        (c.claim_key && zhByKey.get(c.claim_key)) ||
        c.claim_zh?.trim() ||
        '';
      if (z) n += 1;
    }
    return n;
  }, [claims, zhByKey]);
  const pageMeta = themePages?.pages.find((p) => p.label === theme);
  const hasSectionsZh = !!(pageMeta?.sections_zh && pageMeta.sections_zh.length > 0);

  // Anchor handling: #<claim_id> scrolls to + highlights the claim card
  // (same pattern as the source page's unit line anchors). Scroll fires
  // ONCE per hash value — the ref guard keeps claims/model refreshes from
  // yanking the viewport back to the anchor while the user reads.
  const [anchor, setAnchor] = useState<string | null>(null);
  const scrolledHashRef = useRef<string | null>(null);
  useEffect(() => {
    const id = decodeURIComponent(location.hash.replace(/^#/, ''));
    if (!id) {
      setAnchor(null);
      scrolledHashRef.current = null;
      return;
    }
    setAnchor(id);
    if (scrolledHashRef.current === location.hash) return;
    scrolledHashRef.current = location.hash;
    // The cards render in this same commit; scroll on the next frame.
    const frame = requestAnimationFrame(() => {
      document
        .getElementById(id)
        ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
    return () => cancelAnimationFrame(frame);
  }, [location.hash]);

  const durable = claims.filter((c) => c.status === 'durable').length;

  return (
    <div className="grid two-col theme-detail-layout">
      <div className="theme-main">
        <TopicOverview theme={theme} />
        {claims.length === 0 ? (
          <EmptyState>
            <p>{t('theme.empty')}</p>
            <Link className="tiny" to="/knowledge">
              {t('theme.backToKnowledge')} →
            </Link>
          </EmptyState>
        ) : (
          <>
            <div className="claim-meta theme-claims-meta">
              {t('theme.counts', {
                durable,
                caveated: claims.length - durable,
              })}
              {' · '}
              {t('theme.contentLang', {
                lang: lang === 'zh' ? '中文' : 'EN',
                zh: zhCount,
                total: claims.length,
              })}
            </div>
            {lang === 'zh' && zhCount === 0 && (
              <div className="card theme-zh-missing sm">
                <p style={{ margin: 0 }}>
                  {t('theme.zhMissingBody', { n: claims.length })}
                </p>
                <p className="tiny muted" style={{ margin: '0.4rem 0 0' }}>
                  {t('theme.zhMissingHint')}
                </p>
                <p style={{ margin: '0.5rem 0 0' }}>
                  <button
                    type="button"
                    className="linkish tiny"
                    onClick={() => setLang('en')}
                  >
                    {t('theme.switchToEn')}
                  </button>
                </p>
              </div>
            )}
            {lang === 'zh' && zhCount > 0 && !hasSectionsZh && pageMeta && (
              <p className="sm muted theme-zh-partial">
                {t('theme.zhPartialOverview')}
              </p>
            )}
            {claims.map((c) => (
              <ClaimCard
                key={c.claim_id}
                claim={c}
                byCase={byCase}
                highlighted={anchor === c.claim_id}
                claimZh={
                  (c.claim_key && zhByKey.get(c.claim_key)) || c.claim_zh || null
                }
              />
            ))}
          </>
        )}
      </div>
      <aside className="theme-rail">
        <div className="card theme-rail-card">
          <h3 style={{ marginBottom: '0.6rem' }}>{t('theme.graph')}</h3>
          <KnowledgeGraph scope="theme" id={theme} height={360} />
          <div className="graph-caption">{t('theme.graphCaption')}</div>
        </div>
      </aside>
    </div>
  );
}

export default function ThemeDetailPage() {
  const { t } = useI18n();
  const { theme: rawTheme } = useParams<{ theme: string }>();
  // The '' (no-theme) bucket travels as a sentinel segment — decode it back so
  // themeClaims filters the unthemed claims and the page renders as Unclassified.
  const theme = themeFromRoute(rawTheme);
  const { model, error, loading } = useModel();

  // 'misc' displays honestly as "Unclassified"; the route param and all
  // claim data keep the literal theme key (display layer only).
  const misc = isMiscTheme(theme);
  const displayName = misc
    ? t('theme.unclassified')
    : theme || t('knowledge.untitledTheme');

  return (
    <ModelGate loading={loading} error={error}>
      {model && (
        <>
          <div className="crumbs">
            <Link to="/knowledge">{t('nav.knowledge')}</Link> / {displayName}
          </div>
          <h1>{displayName}</h1>
          {misc && (
            <p className="muted tiny" style={{ marginTop: '-0.35rem' }}>
              {t('theme.unclassifiedNote')}
            </p>
          )}
          <ThemeBody model={model} theme={theme} />
        </>
      )}
    </ModelGate>
  );
}
