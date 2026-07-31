import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { ProfilesPanel } from '../components/ProfilesPanel'

const PROFILES = [
  {
    name: 'developer',
    description: 'Implements features',
    source: 'built-in',
    loadable: true,
    role: 'developer',
    tags: ['code'],
    capabilities: ['write code'],
  },
  {
    name: 'reviewer',
    description: 'Reviews PRs',
    source: 'local',
    loadable: true,
    role: 'reviewer',
    tags: [],
    capabilities: [],
  },
  {
    name: 'broken',
    description: 'Cannot load',
    source: 'local',
    loadable: false,
    role: '',
    tags: [],
    capabilities: [],
  },
]

const DETAIL = {
  name: 'developer',
  description: 'Implements features',
  provider: 'claude_code',
  role: 'developer',
  system_prompt: 'You are a careful developer.',
  tags: ['code'],
  capabilities: ['write code'],
}

describe('ProfilesPanel', () => {
  const mockFetch = vi.fn()

  beforeEach(() => {
    vi.stubGlobal('fetch', mockFetch)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  function mockJson(data: unknown, status = 200) {
    return {
      ok: status >= 200 && status < 300,
      status,
      statusText: status === 200 ? 'OK' : 'Error',
      json: () => Promise.resolve(data),
    }
  }

  /** Route by URL so Strict Mode double-mounts and detail fetches stay correct. */
  function mockRoutes(list: unknown = PROFILES) {
    mockFetch.mockImplementation(async (url: string) => {
      if (url === '/agents/profiles') return mockJson(list)
      if (url === '/agents/profiles/developer') return mockJson(DETAIL)
      return mockJson({ detail: 'not found' }, 404)
    })
  }

  it('renders profile rows after fetch', async () => {
    mockRoutes()
    render(<ProfilesPanel />)
    expect(await screen.findByLabelText('Profile developer')).toBeInTheDocument()
    expect(screen.getByLabelText('Profile reviewer')).toBeInTheDocument()
    expect(screen.getByText('Implements features')).toBeInTheDocument()
  })

  it('shows empty state when no profiles', async () => {
    mockRoutes([])
    render(<ProfilesPanel />)
    expect(await screen.findByText('No profiles found.')).toBeInTheDocument()
  })

  it('filters rows client-side', async () => {
    mockRoutes()
    render(<ProfilesPanel />)
    await screen.findByLabelText('Profile developer')
    fireEvent.change(screen.getByPlaceholderText('Filter profiles...'), {
      target: { value: 'review' },
    })
    expect(screen.queryByLabelText('Profile developer')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Profile reviewer')).toBeInTheDocument()
  })

  it('loads and shows manifest when a profile is expanded', async () => {
    mockRoutes()
    render(<ProfilesPanel />)
    await screen.findByLabelText('Profile developer')
    fireEvent.click(screen.getByLabelText('Profile developer'))
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        '/agents/profiles/developer',
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      )
    })
    expect(await screen.findByText('claude_code')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: 'System prompt' }))
    expect(screen.getByText('You are a careful developer.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: 'Manifest JSON' }))
    expect(screen.getByText(/"system_prompt"/)).toBeInTheDocument()
  })

  it('shows unloadable notice without fetching detail', async () => {
    mockRoutes()
    render(<ProfilesPanel />)
    await screen.findByLabelText('Profile broken')
    const listCalls = mockFetch.mock.calls.filter(([url]) => url === '/agents/profiles').length
    fireEvent.click(screen.getByLabelText('Profile broken'))
    expect(
      await screen.findByText(/listed but cannot be loaded/i),
    ).toBeInTheDocument()
    expect(
      mockFetch.mock.calls.filter(([url]) => String(url).includes('/agents/profiles/broken')),
    ).toHaveLength(0)
    expect(mockFetch.mock.calls.filter(([url]) => url === '/agents/profiles').length).toBe(listCalls)
  })
})
