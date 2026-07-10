/** Search `/search` — answers US2 (design §3.4). The page host of the
 * SearchOmnibox; the same component opens as the global ⌘K overlay from
 * the Shell. Query state is URL-parameterized (?q=) so searches are
 * shareable, like every other portal filter. */
import SearchOmnibox from '../components/SearchOmnibox';
import { PageHelp } from '../components/ui';
import { useI18n } from '../i18n';

export default function SearchPage() {
  const { t } = useI18n();
  return (
    <>
      <h1 style={{ marginTop: '1rem' }}>{t('search.title')}</h1>
      <PageHelp>{t('search.help')}</PageHelp>
      <SearchOmnibox variant="page" />
    </>
  );
}
