import { useState, useEffect, useMemo } from 'react'
import { api, AgentProfileInfo, AgentProfileDetail } from '../api'
import { useStore } from '../store'
import { CustomSelect } from './CustomSelect'
import { Package, Search, ChevronDown, ChevronRight, FileText, AlertTriangle } from 'lucide-react'

const SOURCE_LABELS: Record<string, string> = {
  'built-in': 'Built-in',
  local: 'Local',
  kiro: 'Kiro',
  q_cli: 'Q CLI',
  opencode_cli: 'OpenCode',
  claude_code: 'Claude Code',
  cursor_cli: 'Cursor',
  codex: 'Codex',
  copilot_cli: 'Copilot',
  kimi_cli: 'Kimi',
  hermes: 'Hermes',
}

const SOURCE_PILL: Record<string, string> = {
  'built-in': 'bg-emerald-900/50 text-emerald-400',
  local: 'bg-blue-900/50 text-blue-400',
}

function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] || source
}

function sourcePill(source: string): string {
  return SOURCE_PILL[source] || 'bg-gray-700 text-gray-400'
}

/** Fields shown as structured chips; everything else goes into the raw JSON manifest. */
const HIGHLIGHT_KEYS = new Set([
  'name',
  'description',
  'provider',
  'role',
  'model',
  'skills',
  'capabilities',
  'tags',
  'allowedTools',
  'system_prompt',
])

export function ProfilesPanel() {
  const { showSnackbar } = useStore()

  const [profiles, setProfiles] = useState<AgentProfileInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [sourceFilter, setSourceFilter] = useState('')

  const [expandedName, setExpandedName] = useState<string | null>(null)
  const [detail, setDetail] = useState<{ name: string; data: AgentProfileDetail } | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [manifestTab, setManifestTab] = useState<'overview' | 'prompt' | 'json'>('overview')

  useEffect(() => {
    api
      .listProfiles()
      .then(data => setProfiles(Array.isArray(data) ? data : []))
      .catch(() => {
        setProfiles([])
        showSnackbar({ type: 'error', message: 'Failed to load profiles' })
      })
      .finally(() => setLoading(false))
  }, [])

  const sourceOptions = useMemo(() => {
    const sources = Array.from(new Set(profiles.map(p => p.source))).sort()
    return [
      { value: '', label: 'All sources' },
      ...sources.map(s => ({ value: s, label: sourceLabel(s) })),
    ]
  }, [profiles])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return profiles.filter(p => {
      if (sourceFilter && p.source !== sourceFilter) return false
      if (!q) return true
      const hay = [
        p.name,
        p.description,
        p.role || '',
        ...(p.tags || []),
        ...(p.capabilities || []),
      ]
        .join(' ')
        .toLowerCase()
      return hay.includes(q)
    })
  }, [profiles, search, sourceFilter])

  const handleExpand = async (p: AgentProfileInfo) => {
    if (expandedName === p.name) {
      setExpandedName(null)
      return
    }
    setExpandedName(p.name)
    setDetail(null)
    setManifestTab('overview')
    if (p.loadable === false) {
      setDetailLoading(false)
      return
    }
    setDetailLoading(true)
    try {
      const d = await api.getProfile(p.name)
      setExpandedName(current => {
        if (current === p.name) setDetail({ name: p.name, data: d })
        return current
      })
    } catch (e: any) {
      showSnackbar({ type: 'error', message: e.detail || e.message || 'Failed to load profile' })
    } finally {
      setDetailLoading(false)
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-white flex items-center gap-2">
          <Package size={18} className="text-blue-400" />
          Profiles
        </h2>
        <p className="text-xs text-gray-500 mt-1">
          Browse installed agent profiles and inspect each profile&apos;s manifest (frontmatter + system prompt).
        </p>
      </div>

      <div className="bg-gray-800/60 border border-gray-700/50 rounded-xl p-5">
        <div className="flex items-center justify-between mb-4 gap-3 flex-wrap">
          <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">
            Profiles ({filtered.length}
            {filtered.length !== profiles.length ? ` of ${profiles.length}` : ''})
          </h3>
          <div className="flex items-center gap-3 flex-wrap">
            <CustomSelect
              value={sourceFilter}
              onChange={setSourceFilter}
              options={sourceOptions}
              className="w-40"
            />
            <div className="relative">
              <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-500" />
              <input
                type="text"
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="Filter profiles..."
                aria-label="Filter profiles"
                className="bg-gray-900 border border-gray-700 text-gray-200 text-xs rounded-lg pl-8 pr-3 py-1.5 w-56 focus:border-emerald-500 focus:outline-none"
              />
            </div>
          </div>
        </div>

        {loading ? (
          <div className="text-gray-500 text-sm py-8 text-center">Loading profiles...</div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-8">
            <Package size={32} className="mx-auto text-gray-600 mb-3" />
            <p className="text-gray-500 text-sm">No profiles found.</p>
            <p className="text-gray-600 text-xs mt-1">
              Install one with <code className="text-emerald-400">cao install &lt;name&gt;</code> or add a directory in Settings.
            </p>
          </div>
        ) : (
          <div className="space-y-2">
            {filtered.map(p => (
              <div key={p.name} className="bg-gray-900/50 border border-gray-700/30 rounded-lg">
                <button
                  type="button"
                  className="w-full flex items-center justify-between p-3 text-left cursor-pointer hover:bg-gray-800/50 transition-colors rounded-lg"
                  onClick={() => handleExpand(p)}
                  aria-expanded={expandedName === p.name}
                  aria-label={`Profile ${p.name}`}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <FileText size={14} className="text-gray-400 shrink-0" />
                    <span className="text-sm text-gray-200 font-medium truncate" data-testid={`profile-name-${p.name}`}>
                      {p.name}
                    </span>
                    <span className={`text-xs px-2 py-0.5 rounded-full shrink-0 ${sourcePill(p.source)}`}>
                      {sourceLabel(p.source)}
                    </span>
                    {p.role && (
                      <span className="text-xs px-2 py-0.5 rounded-full shrink-0 bg-purple-900/40 text-purple-300">
                        {p.role}
                      </span>
                    )}
                    {p.loadable === false && (
                      <span className="text-xs px-2 py-0.5 rounded-full shrink-0 bg-amber-900/40 text-amber-300 flex items-center gap-1">
                        <AlertTriangle size={10} /> unloadable
                      </span>
                    )}
                    {p.description && (
                      <span className="text-xs text-gray-500 truncate hidden sm:inline">{p.description}</span>
                    )}
                  </div>
                  <div className="shrink-0 ml-3">
                    {expandedName === p.name ? (
                      <ChevronDown size={14} className="text-gray-500" />
                    ) : (
                      <ChevronRight size={14} className="text-gray-500" />
                    )}
                  </div>
                </button>

                {expandedName === p.name && (
                  <div className="px-3 pb-3 text-xs text-gray-400 space-y-3 border-t border-gray-700/30 pt-3">
                    {p.duplicated_in && p.duplicated_in.length > 0 && (
                      <div className="text-amber-400/90">
                        Also defined in: {p.duplicated_in.map(sourceLabel).join(', ')} (this source wins)
                      </div>
                    )}

                    {(p.tags?.length || p.capabilities?.length) ? (
                      <div className="flex flex-wrap gap-1.5">
                        {(p.tags || []).map(t => (
                          <span key={`tag-${t}`} className="px-2 py-0.5 rounded bg-gray-800 text-gray-400 border border-gray-700/50">
                            #{t}
                          </span>
                        ))}
                        {(p.capabilities || []).map(c => (
                          <span key={`cap-${c}`} className="px-2 py-0.5 rounded bg-cyan-950/40 text-cyan-400/80 border border-cyan-900/40">
                            {c}
                          </span>
                        ))}
                      </div>
                    ) : null}

                    {p.loadable === false ? (
                      <div className="bg-amber-950/30 border border-amber-800/40 rounded-lg p-3 text-amber-200/90">
                        This profile is listed but cannot be loaded (invalid or unresolved source).
                      </div>
                    ) : detailLoading || !detail || detail.name !== p.name ? (
                      <div className="text-gray-500">Loading manifest...</div>
                    ) : (
                      <ManifestView
                        data={detail.data}
                        tab={manifestTab}
                        onTabChange={setManifestTab}
                      />
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function ManifestView({
  data,
  tab,
  onTabChange,
}: {
  data: AgentProfileDetail
  tab: 'overview' | 'prompt' | 'json'
  onTabChange: (t: 'overview' | 'prompt' | 'json') => void
}) {
  const extraKeys = Object.keys(data).filter(k => !HIGHLIGHT_KEYS.has(k))

  return (
    <div className="space-y-3">
      <div className="flex gap-1" role="tablist" aria-label="Manifest view">
        {(
          [
            ['overview', 'Overview'],
            ['prompt', 'System prompt'],
            ['json', 'Manifest JSON'],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            onClick={() => onTabChange(key)}
            className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
              tab === key
                ? 'bg-emerald-600/80 text-white'
                : 'bg-gray-800 text-gray-400 hover:text-gray-200'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'overview' && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2">
          <Field label="Name" value={data.name} mono />
          <Field label="Provider" value={data.provider || '—'} />
          <Field label="Role" value={data.role || '—'} />
          <Field label="Model" value={data.model || '—'} />
          <div className="sm:col-span-2">
            <Field label="Description" value={data.description || '—'} />
          </div>
          {data.skills?.length ? (
            <div className="sm:col-span-2">
              <Field label="Skills" value={data.skills.join(', ')} mono />
            </div>
          ) : null}
          {data.allowedTools?.length ? (
            <div className="sm:col-span-2">
              <Field label="Allowed tools" value={data.allowedTools.join(', ')} mono />
            </div>
          ) : null}
          {extraKeys.length > 0 && (
            <div className="sm:col-span-2 text-gray-500">
              Additional fields: {extraKeys.join(', ')} (see Manifest JSON)
            </div>
          )}
        </div>
      )}

      {tab === 'prompt' && (
        <div className="bg-gray-950/60 border border-gray-700/30 rounded-lg p-3 text-sm text-gray-300 font-mono whitespace-pre-wrap leading-relaxed max-h-96 overflow-y-auto">
          {data.system_prompt?.trim() ? data.system_prompt : '(no system prompt)'}
        </div>
      )}

      {tab === 'json' && (
        <pre className="bg-gray-950/60 border border-gray-700/30 rounded-lg p-3 text-sm text-gray-300 font-mono leading-relaxed max-h-96 overflow-auto">
          {JSON.stringify(data, null, 2)}
        </pre>
      )}
    </div>
  )
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <span className="text-gray-500">{label}: </span>
      <span className={`text-gray-300 ${mono ? 'font-mono' : ''}`}>{value}</span>
    </div>
  )
}
