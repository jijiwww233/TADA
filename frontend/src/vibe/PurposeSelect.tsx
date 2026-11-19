const labels: Record<string, string> = { layout: '排版', palette: '配色', subject: '主体', style: '风格' }

export function PurposeSelect({ value, onChange, disabled }: {
  value: string; onChange: (value: string) => void; disabled: boolean
}) {
  return <label>用途{' '}
    <select value={value} disabled={disabled} onChange={event => onChange(event.currentTarget.value)}>
      <option value="">请选择</option>
      {Object.entries(labels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
    </select>
  </label>
}
