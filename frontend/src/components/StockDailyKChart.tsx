import { useCallback, useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, type KlineRow } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import {
  EChartsCandlestick,
  OVERLAY_INDICATORS,
  SUB_CHARTS,
  type ChartMarker,
  type ChartPriceLine,
  type ChartRange,
  type OHLC,
  type StockInfo,
} from '@/components/EChartsCandlestick'

const SUB_INFO_H = 16
const SUB_GAP = 4
const MAX_DAYS = 2000

export interface StockDailyKChartResult {
  rows: OHLC[]
  rawRows: KlineRow[]
  stockInfo?: StockInfo
  name?: string
}

interface Props {
  symbol: string
  height?: number
  className?: string
  dateRange?: { start: string; end: string }
  markers?: ChartMarker[]
  ranges?: ChartRange[]
  priceLines?: ChartPriceLine[]
  showLimitMarkers?: boolean
  showIndicatorControls?: boolean
  showMarkerToggle?: boolean
  showMA?: boolean
  showInfoBar?: boolean
  visibleBars?: number
  linkedPrice?: number | null
  onDateClick?: (date: string) => void
  onDataChange?: (result: StockDailyKChartResult) => void
  /** 扩展数据列参数（逗号分隔 config_id.field_name），透传给 klineDaily 接口 */
  extColumns?: string
}

function toFiniteNumber(value: unknown): number | null {
  // K 线价格与指标统一入口：过滤 null、NaN 和无穷大，避免图表渲染异常。
  if (value == null || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function isValidRow(r: any): boolean {
  const open = toFiniteNumber(r?.open)
  const high = toFiniteNumber(r?.high)
  const low = toFiniteNumber(r?.low)
  const close = toFiniteNumber(r?.close)
  return !!r && r.date != null && open != null && high != null && low != null && close != null
    && open > 0 && high > 0 && low > 0 && close > 0
}

export function toOHLC(rows: KlineRow[]): OHLC[] {
  return rows
    .filter(isValidRow)
    .map(r => {
      const open = toFiniteNumber(r.open)!
      const high = toFiniteNumber(r.high)!
      const low = toFiniteNumber(r.low)!
      const close = toFiniteNumber(r.close)!
      return {
        date: typeof r.date === 'string' ? r.date.slice(0, 10) : String(r.date),
        open,
        high,
        low,
        close,
        volume: toFiniteNumber(r.volume) ?? 0,
        ma5: toFiniteNumber(r.ma5),
        ma10: toFiniteNumber(r.ma10),
        ma20: toFiniteNumber(r.ma20),
        ma60: toFiniteNumber(r.ma60),
        macd_dif: toFiniteNumber(r.macd_dif),
        macd_dea: toFiniteNumber(r.macd_dea),
        macd_hist: toFiniteNumber(r.macd_hist),
        rsi_6: toFiniteNumber(r.rsi_6),
        rsi_14: toFiniteNumber(r.rsi_14),
        rsi_24: toFiniteNumber(r.rsi_24),
        kdj_k: toFiniteNumber(r.kdj_k),
        kdj_d: toFiniteNumber(r.kdj_d),
        kdj_j: toFiniteNumber(r.kdj_j),
        boll_upper: toFiniteNumber(r.boll_upper),
        boll_lower: toFiniteNumber(r.boll_lower),
      }
    })
}

function buildLimitUpMarkers(rows: KlineRow[]): ChartMarker[] {
  const markers: ChartMarker[] = []
  for (const r of rows) {
    const date = typeof r.date === 'string' ? r.date.slice(0, 10) : String(r.date)
    if (r.signal_broken_limit_up) {
      markers.push({ date, kind: 'neutral', above: true, color: '#8B5CF6', label: '炸' })
    } else if (r.signal_limit_up) {
      const boards: number = r.consecutive_limit_ups ?? 1
      markers.push({ date, kind: 'buy', above: true, color: '#FACC15', label: boards <= 1 ? '板' : String(boards) })
    }
  }
  return markers
}

export function getDefaultRange(): { start: string; end: string } {
  const now = new Date()
  const end = now.toISOString().slice(0, 10)
  const s = new Date(now)
  s.setMonth(s.getMonth() - 6)
  const start = s.toISOString().slice(0, 10)
  return { start, end }
}

function rangeDays(range: { start: string; end: string }): number {
  const start = new Date(range.start)
  const end = new Date(range.end)
  return Math.min(Math.ceil((end.getTime() - start.getTime()) / 86400000) + 30, MAX_DAYS)
}

export function StockDailyKChart({
  symbol,
  height = 520,
  className,
  dateRange: externalDateRange,
  markers,
  ranges,
  priceLines,
  showLimitMarkers = true,
  showIndicatorControls = true,
  showMarkerToggle = true,
  showMA = true,
  showInfoBar = true,
  visibleBars = 60,
  linkedPrice,
  onDateClick,
  onDataChange,
  extColumns,
}: Props) {
  const [activeIndicators, setActiveIndicators] = useState<string[]>(['vol'])
  const [showMarkers, setShowMarkers] = useState(true)
  const dateRange = externalDateRange ?? getDefaultRange()
  const days = useMemo(() => rangeDays(dateRange), [dateRange])

  // extColumns 纳入 query key：勾选/取消扩展字段时需重新请求（带 ext_columns 参数）
  const kline = useQuery({
    queryKey: QK.kline(symbol, dateRange.start, dateRange.end, extColumns),
    queryFn: () => api.klineDaily(symbol, days, dateRange, extColumns),
    enabled: !!symbol,
    placeholderData: (prev) => prev,
  })

  const rows = useMemo(() => toOHLC(kline.data?.rows ?? []), [kline.data?.rows])
  const stockInfo = kline.data?.stock_info
  const limitMarkers = useMemo(() => buildLimitUpMarkers(kline.data?.rows ?? []), [kline.data?.rows])
  const allMarkers = useMemo(() => [
    ...(markers ?? []),
    ...(showLimitMarkers ? limitMarkers : []),
  ], [limitMarkers, markers, showLimitMarkers])

  const toggleIndicator = useCallback((key: string) => {
    setActiveIndicators(prev => prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key])
  }, [])

  const activeSubDefs = activeIndicators
    .map(key => SUB_CHARTS.find(s => s.key === key))
    .filter((d): d is typeof SUB_CHARTS[number] => !!d)
  let subExtraH = 0
  activeSubDefs.forEach(def => { subExtraH += SUB_INFO_H + def.height })
  if (activeSubDefs.length > 0) subExtraH += activeSubDefs.length * SUB_GAP + 14
  const chartHeight = height + subExtraH

  useEffect(() => {
    onDataChange?.({ rows, rawRows: kline.data?.rows ?? [], stockInfo, name: kline.data?.name })
  }, [kline.data?.name, kline.data?.rows, onDataChange, rows, stockInfo])

  if (!symbol) return null

  return (
    <div className={className} style={{ minHeight: chartHeight }}>
      {showIndicatorControls && rows.length > 0 && (
        <div className="flex items-center gap-1.5 px-1 pb-0.5">
          {SUB_CHARTS.map(ind => (
            <button
              key={ind.key}
              onClick={() => toggleIndicator(ind.key)}
              className={`px-2 py-0.5 rounded text-[10px] font-mono cursor-pointer transition-colors ${
                activeIndicators.includes(ind.key)
                  ? 'bg-accent/20 text-accent'
                  : 'bg-elevated text-muted hover:text-secondary'
              }`}
            >
              {ind.label}
            </button>
          ))}
          {OVERLAY_INDICATORS.map(ind => (
            <button
              key={ind.key}
              onClick={() => toggleIndicator(ind.key)}
              className={`px-2 py-0.5 rounded text-[10px] font-mono cursor-pointer transition-colors ${
                activeIndicators.includes(ind.key)
                  ? 'bg-accent/20 text-accent'
                  : 'bg-elevated text-muted hover:text-secondary'
              }`}
            >
              {ind.label}
            </button>
          ))}
          {showMarkerToggle && showLimitMarkers && (
            <button
              onClick={() => setShowMarkers(v => !v)}
              className={`ml-auto px-2 py-0.5 rounded text-[10px] font-mono cursor-pointer transition-colors ${
                showMarkers
                  ? 'text-[#FACC15] bg-[#FACC15]/10'
                  : 'bg-elevated text-muted hover:text-secondary'
              }`}
            >
              异动
            </button>
          )}
        </div>
      )}
      {kline.isLoading && <div className="text-sm text-muted py-4">加载中…</div>}
      {kline.isError && <div className="text-sm text-danger py-2">日K加载失败</div>}
      {!kline.isLoading && !kline.isError && (kline.data?.rows?.length ?? 0) > 0 && rows.length === 0 && (
        <div className="text-sm text-danger py-2">数据格式异常，请刷新页面</div>
      )}
      {rows.length > 0 && (
        <EChartsCandlestick
          data={rows}
          markers={allMarkers}
          ranges={ranges}
          priceLines={priceLines}
          height={chartHeight - 22}
          showMA={showMA}
          showInfoBar={showInfoBar}
          showMarkers={showMarkers}
          stockInfo={stockInfo}
          symbol={symbol}
          linkedPrice={linkedPrice}
          onDateClick={onDateClick}
          visibleBars={visibleBars}
          activeIndicators={activeIndicators}
        />
      )}
    </div>
  )
}
