import type { AskProgressHit } from '../lib/types';
import { useI18n } from '../i18n';

/** Preserve the provider number, including zero; never infer a missing value. */
export function decisionNumber(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1
    ? String(value) : 'unknown';
}

export default function SemanticAssessments({ hits }: { hits: AskProgressHit[] }) {
  const { t } = useI18n();
  const judged = hits.filter(h => h.semantic_evidence);
  if (!judged.length) return null;
  return <section className="card" aria-label={t('ask.semanticTitle')}>
    <h3>{t('ask.semanticTitle')}</h3>
    <p className="tiny muted">{t('ask.semanticHelp')}</p>
    {judged.map((hit, index) => {
      const s = hit.semantic_evidence!;
      return <details className="cite-entry" key={`${hit.id}:${index}`}>
        <summary>
          {hit.label}
          <div className="tiny muted">
            {t('ask.semanticConfidence')}: <span className="mono">{decisionNumber(s.relevance_statistics?.confidence)}</span>
            {' · '}{t('ask.semanticProbability')}: <span className="mono">{decisionNumber(s.relevance_statistics?.selected_probability)}</span>
          </div>
        </summary>
        {([['relevance', 'ask.semanticRelevance'], ['relation', 'ask.semanticRelation']] as const).map(([key, label]) => {
          const stats = s[`${key}_statistics`];
          return <div key={key} className="tiny">
            <strong>{t(label)}: {s[key]}</strong>
            <div>{t('ask.semanticConfidence')}: <span className="mono">{decisionNumber(stats?.confidence)}</span></div>
            <div>{t('ask.semanticProbability')}: <span className="mono">{decisionNumber(stats?.selected_probability)}</span></div>
          </div>;
        })}
        <div className="tiny muted">{s.provider ?? 'unknown'} · {s.model ?? 'unknown'} · {s.question_namespace ?? 'unknown'}</div>
      </details>;
    })}
  </section>;
}
