export const TEST_LABELS = {
  precision: '定位精度', tracking: '连续跟踪', switching: '目标切换', recognition: '敌我识别',
  'move-fire': '移动射击', 'last-hit': '补刀时机', 'orb-walk': '走砍节奏', dodge: '威胁躲避',
  'map-awareness': '小地图注意', 'skill-aim': '技能瞄准', 'move-attack': '移动攻击',
} as const

export type TestId = keyof typeof TEST_LABELS
export type CatalogRevision = 1 | 2 | 3

export function projectSelection(test: TestId, catalogRevision: CatalogRevision = 3) {
  const name = catalogRevision === 3 && test === 'dodge' ? '危险区域识别与躲避' : TEST_LABELS[test]
  return { name, exact: true } as const
}
