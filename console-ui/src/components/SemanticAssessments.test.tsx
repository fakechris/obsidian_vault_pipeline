import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { I18nProvider } from '../i18n';
import SemanticAssessments, { decisionNumber } from './SemanticAssessments';

describe('provider judgment display', () => {
  it('shows raw supplied values, zero, unknown and lineage without calling them correctness', () => {
    const html = renderToStaticMarkup(<I18nProvider><SemanticAssessments hits={[{
      kind:'source', id:'s1', label:'Example source', semantic_evidence:{
        relevance:'direct', relation:'contextual', provider:'typesafe',model:'jev-1.13.0',question_namespace:'evidence_relevance/v1',
        relevance_statistics:{confidence:0,selected_probability:0.6}, relation_statistics:{confidence:null,selected_probability:null},
      },
    }]} /></I18nProvider>);
    expect(html).toContain('Selected option probability');
    expect(html).toContain('>0<');
    expect(html).toContain('>0.6<');
    expect(html).toContain('unknown');
    expect(html).toContain('jev-1.13.0');
    expect(html).toContain('not correctness scores');
  });
  it('supplies no numbers for unjudged fallback or abstain results', () => {
    expect(renderToStaticMarkup(<I18nProvider><SemanticAssessments hits={[{kind:'source',id:'s1',label:'Unjudged'}]} /></I18nProvider>)).toBe('');
    for (const value of [null,undefined,NaN,Infinity,-0.1,1.1]) expect(decisionNumber(value)).toBe('unknown');
  });
});
