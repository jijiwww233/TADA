import LocalImportPanel from './localImport/LocalImportPanel'

interface Props {
  onImported: () => void
}

/** Thin compatibility wrapper: the real UI lives in localImport/LocalImportPanel. */
export default function DropZone({ onImported }: Props) {
  return <LocalImportPanel onImported={onImported} />
}
