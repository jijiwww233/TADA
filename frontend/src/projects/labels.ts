export const purposeLabel = (value: string) => ({ layout: '排版', style: '风格', palette: '配色', subject: '主题/内容' } as Record<string, string>)[value] ?? value
