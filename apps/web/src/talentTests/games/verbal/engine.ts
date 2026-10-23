export type VerbalMemoryAnswer = "seen" | "new"

export type RandomSource = () => number

export interface VerbalMemoryState {
  currentWord: string
  seenWords: ReadonlySet<string>
  lives: number
  score: number
  turn: number
  finished: boolean
}

export const CHINESE_WORDS = [
  "山川", "海洋", "书包", "太阳", "月亮", "星星", "天空", "云朵", "雨伞", "雪花",
  "河流", "湖泊", "森林", "草原", "沙漠", "岛屿", "海岸", "瀑布", "山谷", "田野",
  "花园", "公园", "道路", "桥梁", "隧道", "城市", "村庄", "房屋", "门窗", "桌椅",
  "沙发", "床铺", "衣柜", "厨房", "餐厅", "学校", "教室", "操场", "图书馆", "运动场",
  "医院", "商店", "市场", "超市", "银行", "邮局", "车站", "机场", "港口", "工厂",
  "公司", "办公室", "父亲", "母亲", "兄弟", "姐妹", "爷爷", "奶奶", "朋友", "老师",
  "学生", "医生", "护士", "警察", "司机", "厨师", "画家", "作家", "歌手", "演员",
  "农民", "工人", "客人", "邻居", "同学", "铅笔", "钢笔", "橡皮", "尺子", "课本",
  "书本", "报纸", "地图", "相册", "信封", "邮票", "钥匙", "钱包", "手表", "眼镜",
  "帽子", "衬衫", "裤子", "鞋子", "袜子", "毛巾", "牙刷", "杯子", "碗筷", "盘子",
  "勺子", "锅子", "菜刀", "冰箱", "电视", "电脑", "手机", "相机", "耳机", "台灯",
  "风扇", "空调", "钟表", "玩具", "积木", "皮球", "风筝", "雨衣", "自行车", "轿车",
  "火车", "飞机", "轮船", "公交车", "苹果", "香蕉", "葡萄", "西瓜", "草莓", "橙子",
  "桃子", "梨子", "樱桃", "柠檬", "蔬菜", "白菜", "萝卜", "土豆", "番茄", "黄瓜",
  "南瓜", "米饭", "面条", "馒头", "饺子", "面包", "蛋糕", "饼干", "糖果", "巧克力",
  "牛奶", "豆浆", "茶叶", "咖啡", "鸡蛋", "豆腐", "猪肉", "牛肉", "鱼肉", "小猫",
  "小狗", "熊猫", "老虎", "狮子", "大象", "长颈鹿", "猴子", "兔子", "松鼠", "狐狸",
  "狼群", "马匹", "牛羊", "山羊", "绵羊", "小鸟", "鸽子", "燕子", "老鹰", "孔雀",
  "天鹅", "鸭子", "公鸡", "蝴蝶", "蜜蜂", "蚂蚁", "蜻蜓", "金鱼", "鲸鱼", "海豚",
  "乌龟", "青蛙",
] as const

function randomItem(items: readonly string[], random: RandomSource): string {
  const rawIndex = Math.floor(random() * items.length)
  const index = Math.max(0, Math.min(items.length - 1, rawIndex))
  return items[index]
}

function nextWord(
  wordBank: readonly string[],
  seenWords: ReadonlySet<string>,
  random: RandomSource,
): string {
  const seen = [...seenWords]
  const unseen = wordBank.filter((word) => !seenWords.has(word))

  if (unseen.length === 0) return randomItem(seen, random)
  if (seen.length === 0) return randomItem(unseen, random)

  return random() < 0.5
    ? randomItem(seen, random)
    : randomItem(unseen, random)
}

export function startVerbalMemory(
  wordBank: readonly string[] = CHINESE_WORDS,
  random: RandomSource = Math.random,
): VerbalMemoryState {
  if (wordBank.length === 0) {
    throw new RangeError("词库不能为空")
  }

  return {
    currentWord: randomItem(wordBank, random),
    seenWords: new Set(),
    lives: 3,
    score: 0,
    turn: 0,
    finished: false,
  }
}

export function answerVerbalMemory(
  state: VerbalMemoryState,
  answer: VerbalMemoryAnswer,
  wordBank: readonly string[] = CHINESE_WORDS,
  random: RandomSource = Math.random,
): VerbalMemoryState {
  if (state.finished) return state

  const wasSeen = state.seenWords.has(state.currentWord)
  const isCorrect = answer === (wasSeen ? "seen" : "new")
  const lives = isCorrect ? state.lives : state.lives - 1
  const score = isCorrect ? state.score + 1 : state.score
  const seenWords = new Set(state.seenWords)
  seenWords.add(state.currentWord)

  if (lives <= 0) {
    return {
      ...state,
      seenWords,
      lives: 0,
      score,
      turn: state.turn + 1,
      finished: true,
    }
  }

  return {
    currentWord: nextWord(wordBank, seenWords, random),
    seenWords,
    lives,
    score,
    turn: state.turn + 1,
    finished: false,
  }
}
