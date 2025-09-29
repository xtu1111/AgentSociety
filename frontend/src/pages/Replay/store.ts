import { makeAutoObservable, runInAction } from "mobx";
import { Agent, AgentDialog, AgentProfile, AgentStatus, AgentSurvey, LngLat, Time, ApiMetric } from "./components/type";
import { message } from "antd";
import React from "react";
import { Experiment, Survey } from "../../components/type";
import { round4 } from "../../components/util";
import { fetchCustom } from "../../components/fetch";

const formatStatus = (status: any) => {
    if (typeof status === 'number') {
        return round4(status)
    } else if (typeof status === 'string') {
        if (status == "") {
            return '-'
        }
        return status
    } else if (status === undefined || status === null) {
        return '-'
    } else if (typeof status === 'object') {
        if (Array.isArray(status)) {
            return JSON.stringify(status.map(s => formatStatus(s)))
        } else {
            return Object.fromEntries(Object.entries(status).map(([k, v]) => [k, formatStatus(v)]))
        }
    }
    return status
}

export class ReplayStore {
    mapCenter: LngLat = {
        lng: 0,
        lat: 0,
    }
    mapCenterDone = false // 是否已经根据agent位置设置了mapCenter
    hasMap = false

    expID?: string
    experiment?: Experiment
    _timeline: Time[] = []
    _currentTime?: Time = undefined
    _agent2Profile: Map<number, AgentProfile> = new Map()
    globalPrompt?: string = undefined
    agents: Map<number, Agent> = new Map()
    clickedAgentID?: number = undefined
    _clickedAgentStatuses: AgentStatus[] = []
    _clickedAgentDialogs: AgentDialog[] = []
    _clickedAgentSurveys: AgentSurvey[] = []
    _metrics: Map<string, ApiMetric[]> = new Map()

    _id2surveys: Map<string, Survey> = new Map()

    heatmapKeyInStatus?: string = undefined

    constructor() {
        makeAutoObservable(this)
    }

    setCenter(center: LngLat) {
        this.mapCenter = center
    }

    setHeatmapKeyInStatus(key?: string) {
        console.log('set heatmap key: ', key)
        this.heatmapKeyInStatus = key
    }

    get timeline() {
        return this._timeline?.slice() ?? []
    }

    async _fetchSurveys() {
        try {
            const res = await fetchCustom('/api/surveys')
            const data = await res.json()
            runInAction(() => {
                const surveys = data.data as Survey[]
                this._id2surveys = new Map(surveys.map(s => [s.id, s]))
            })
        } catch (err) {
            message.error(`Failed to fetch surveys: ${JSON.stringify(err)}`, 3);
            console.error('Failed to fetch surveys: ', err);
        }
    }

    async _fetchExperiment() {
        if (this.expID === undefined) {
            return
        }
        try {
            const res = await fetchCustom(`/api/experiments/${this.expID}`)
            if (!res.ok) {
                throw new Error(`HTTP ${res.status}`)
            }
            const data = await res.json()
            runInAction(() => {
                const experiment = data.data as Experiment
                this.experiment = experiment
                this.hasMap = this._determineHasMap(experiment)
            })
            {
                const res = await fetchCustom(`/api/experiments/${this.expID}/timeline`)
                if (!res.ok) {
                    throw new Error(`HTTP ${res.status}`)
                }
                const data = await res.json()
                runInAction(() => {
                    this._timeline = data.data as Time[]
                    if (this.experiment?.status === 2) {
                        // completed -> set currentTime to the last time
                        this._currentTime = this._timeline[this._timeline.length - 1]
                    } else if (this.experiment?.status === 1) {
                        // running -> set currentTime to the first time
                        this._currentTime = this._timeline[0]
                    }
                })
                if (this._timeline.length === 0) {
                    throw "bad experiment with no timeline"
                }
            }
        } catch (err) {
            const errorMessage = err instanceof Error ? err.message : JSON.stringify(err)
            message.error(`Failed to fetch experiment: ${errorMessage}`, 3);
            console.error('Failed to fetch experiment: ', err);
            runInAction(() => {
                this.hasMap = false
            })
        }
    }

    async _fetchAgentProfile() {
        if (this.expID === undefined) {
            return
        }
        try {
            const res = await fetchCustom(`/api/experiments/${this.expID}/agents/-/profile`)
            const data = await res.json()
            runInAction(() => {
                this._agent2Profile = new Map((data.data as AgentProfile[]).map(a => {
                    a.profile = Object.fromEntries(Object.entries(a.profile).map(([k, v]) => [k, formatStatus(v)]))
                    return [a.id, a]
                })
                )
            })
        } catch (err) {
            message.error(`Failed to fetch agent profile: ${JSON.stringify(err)}`, 3);
            console.error('Failed to fetch agent profile: ', err);
        }
    }

    private _determineHasMap(experiment: Experiment | any): boolean {
        const rawConfigCandidates = [
            (experiment as any)?.config,
            (experiment as any)?.config_base64,
            (experiment as any)?.configBase64,
        ]

        let parsedConfig: any = undefined
        for (const candidate of rawConfigCandidates) {
            if (candidate === null || candidate === undefined) {
                continue
            }
            if (typeof candidate === 'object') {
                parsedConfig = candidate
                break
            }
            if (typeof candidate !== 'string') {
                continue
            }
            const textVariants = this._collectCandidateStrings(candidate)
            for (const variant of textVariants) {
                const parsedJson = this._tryParseJson(variant)
                if (parsedJson !== undefined) {
                    parsedConfig = parsedJson
                    break
                }
            }
            if (parsedConfig !== undefined) {
                break
            }
        }

        const mapSources: any[] = []
        if (parsedConfig && typeof parsedConfig === 'object') {
            if (parsedConfig.map !== undefined) {
                mapSources.push(parsedConfig.map)
            }
            if ((parsedConfig as any).Map !== undefined) {
                mapSources.push((parsedConfig as any).Map)
            }
        }
        mapSources.push((experiment as any)?.map)
        mapSources.push((experiment as any)?.map_config)
        mapSources.push((experiment as any)?.mapConfig)

        for (const source of mapSources) {
            if (this._mapConfigIndicatesPresence(source)) {
                return true
            }
        }

        const textCandidates: string[] = []
        for (const candidate of rawConfigCandidates) {
            if (typeof candidate === 'string') {
                textCandidates.push(...this._collectCandidateStrings(candidate))
            }
        }

        for (const text of textCandidates) {
            if (this._textHasMapIndicator(text)) {
                return true
            }
        }

        return false
    }

    private _collectCandidateStrings(value: string): string[] {
        const trimmed = value.trim()
        if (trimmed.length === 0) {
            return []
        }

        const variants = [trimmed]

        const normalized = trimmed.replace(/\s+/g, '')
        const base64Pattern = /^[A-Za-z0-9+/=]+$/
        if (base64Pattern.test(normalized) && normalized.length % 4 === 0) {
            const globalScope: any = typeof globalThis !== 'undefined' ? globalThis : undefined
            const decoders: Array<(input: string) => string> = []
            if (globalScope?.atob) {
                decoders.push((input) => globalScope.atob(input))
            }
            if (globalScope?.Buffer?.from) {
                decoders.push((input) => globalScope.Buffer.from(input, 'base64').toString('utf-8'))
            }
            for (const decode of decoders) {
                try {
                    const decoded = decode(normalized)
                    if (typeof decoded === 'string' && decoded.length > 0) {
                        variants.push(decoded)
                    }
                } catch {
                    // ignore decoding errors
                }
            }
        }

        return variants
    }

    private _tryParseJson(text: string): any | undefined {
        try {
            return JSON.parse(text)
        } catch {
            return undefined
        }
    }

    private _mapConfigIndicatesPresence(mapConfig: any): boolean {
        if (mapConfig === null || mapConfig === undefined) {
            return false
        }
        if (typeof mapConfig === 'string') {
            const normalized = this._normalizeScalar(mapConfig)
            return normalized !== undefined
        }
        if (Array.isArray(mapConfig)) {
            return mapConfig.some(item => this._mapConfigIndicatesPresence(item))
        }
        if (typeof mapConfig === 'object') {
            const fileKeys = ['file_path', 'filePath', 'path', 'url']
            for (const key of fileKeys) {
                const value = (mapConfig as any)[key]
                if (typeof value === 'string') {
                    const normalized = this._normalizeScalar(value)
                    if (normalized !== undefined) {
                        return true
                    }
                }
            }
            const idKeys = ['map_id', 'mapId', 'id']
            for (const key of idKeys) {
                const value = (mapConfig as any)[key]
                if (typeof value === 'string') {
                    const normalized = this._normalizeScalar(value)
                    if (normalized !== undefined) {
                        return true
                    }
                }
            }
        }
        return false
    }

    private _textHasMapIndicator(text: string): boolean {
        if (typeof text !== 'string') {
            return false
        }

        const lines = text.split(/\r?\n/)
        for (let i = 0; i < lines.length; i++) {
            const line = lines[i]
            const trimmed = line.trim()
            if (trimmed.length === 0 || trimmed.startsWith('#')) {
                continue
            }
            if (!trimmed.startsWith('map:')) {
                continue
            }

            const colonIndex = line.indexOf(':')
            const baseIndent = colonIndex >= 0 ? line.slice(0, colonIndex).search(/\S|$/) : 0
            const inlineValue = trimmed.slice('map:'.length).trim()

            if (inlineValue.length > 0) {
                const inlineMatch = inlineValue.match(/(file_path|filePath|path|map_id|mapId|id)\s*[:=]\s*([^,}]+)[,}]?/i)
                if (inlineMatch) {
                    if (this._normalizeScalar(inlineMatch[2]) !== undefined) {
                        return true
                    }
                } else if (this._normalizeScalar(inlineValue) !== undefined) {
                    return true
                }
            }

            for (let j = i + 1; j < lines.length; j++) {
                const childLine = lines[j]
                const childTrimmed = childLine.trim()
                if (childTrimmed.length === 0 || childTrimmed.startsWith('#')) {
                    continue
                }
                const childIndent = childLine.search(/\S|$/)
                if (childIndent <= baseIndent) {
                    break
                }
                const match = childTrimmed.match(/^(file_path|filePath|path|map_id|mapId|id)\s*:\s*(.+)$/i)
                if (match) {
                    if (this._normalizeScalar(match[2]) !== undefined) {
                        return true
                    }
                }
            }

            break
        }

        return false
    }

    private _normalizeScalar(rawValue: string): string | undefined {
        if (typeof rawValue !== 'string') {
            return undefined
        }
        let normalized = rawValue.trim()
        if (normalized.length === 0) {
            return undefined
        }
        normalized = normalized.replace(/[,'"}\]]+$/g, '').trim()
        if (normalized.startsWith("\"") && normalized.endsWith("\"")) {
            normalized = normalized.slice(1, -1)
        } else if (normalized.startsWith("'") && normalized.endsWith("'")) {
            normalized = normalized.slice(1, -1)
        }
        normalized = normalized.trim()
        if (normalized.length === 0) {
            return undefined
        }
        const lower = normalized.toLowerCase()
        if (lower === 'null' || lower === 'none' || lower === 'undefined') {
            return undefined
        }
        return normalized
    }

    async _fetchAllAgentStatusAndPrompt(time?: Time) {
        if (this.expID === undefined) {
            return
        }
        try {
            let url = `/api/experiments/${this.expID}/agents/-/status`
            if (time !== undefined) {
                url += `?day=${time.day}&t=${time.t}`
            }
            const res = await fetchCustom(url)
            if (!res.ok) {
                throw new Error(`HTTP ${res.status}`)
            }
            const data = await res.json()
            const agentStatuses = data.data as AgentStatus[]
            // if (agentStatuses.length > 0) {
            //     console.log('fetched agent status: ', agentStatuses.length)
            // }
            runInAction(() => {
                let center = {
                    lng: 0,
                    lat: 0,
                }
                let cnt = 0
                const newAgents = new Map<number, Agent>()
                // merge status with profile
                agentStatuses.forEach((status) => {
                    if (!this.mapCenterDone) {
                        if (status.lng !== undefined && status.lng !== null && status.lat !== undefined && status.lat !== null) {
                            center.lng += status.lng
                            center.lat += status.lat
                            cnt += 1
                            console.log('status: ', JSON.stringify(status))
                            console.log('center: ', JSON.stringify(center))
                            console.log('cnt: ', cnt)
                            console.log('mean: ', center.lng / cnt, center.lat / cnt)
                        }
                    }
                    if (typeof status.status === 'object' && status.status !== null) {
                        status.status = Object.fromEntries(Object.entries(status.status).map(([k, v]) => [k, formatStatus(v)]))
                    }
                    // 如果status是字符串，直接保持原样
                    const profile = this._agent2Profile.get(status.id)
                    if (profile !== undefined) {
                        newAgents.set(status.id, { ...profile, ...status })
                    } else {
                        console.error('agent profile not found: ', status.id)
                    }
                })
                this.agents = newAgents
                if (cnt > 0 && !this.mapCenterDone) {
                    center.lng /= cnt
                    center.lat /= cnt
                    this.mapCenter = center
                    console.log('set center: ', JSON.stringify(center))
                    this.mapCenterDone = true
                }
            })
        } catch (err) {
            const errorMessage = err instanceof Error ? err.message : JSON.stringify(err)
            message.error(`Failed to fetch data: ${errorMessage}`, 3);
            console.error('Failed to fetch data: ', err);
        }
        try {
            let url = `/api/experiments/${this.expID}/prompt`
            if (time !== undefined) {
                url += `?day=${time.day}&t=${time.t}`
            }
            const res = await fetchCustom(url)
            let prompt = undefined
            if (res.ok) {
                const data = await res.json()
                if (data.data !== undefined && data.data !== null) {
                    prompt = data.data.prompt
                }
            }
            runInAction(() => {
                this.globalPrompt = prompt
            })
        } catch (err) {
            // message.error(`Failed to fetch prompt: ${JSON.stringify(err)}`, 3);
            console.error('Failed to fetch prompt: ', err);
        }
    }

    async _fetchClickedAgent() {
        if (this.expID === undefined || this.clickedAgentID === undefined) {
            return
        }
        try {
            {
                const res = await fetchCustom(`/api/experiments/${this.expID}/agents/${this.clickedAgentID}/status`)
                const data = await res.json()
                for (const status of data.data as AgentStatus[]) {
                    if (typeof status.status === 'object' && status.status !== null) {
                        status.status = Object.fromEntries(Object.entries(status.status).map(([k, v]) => [k, formatStatus(v)]))
                    }
                    // 如果status是字符串，直接保持原样
                }
                runInAction(() => {
                    this._clickedAgentStatuses = data.data as AgentStatus[]
                })
            }
            {
                const res = await fetchCustom(`/api/experiments/${this.expID}/agents/${this.clickedAgentID}/dialog`)
                const data = await res.json()
                runInAction(() => {
                    this._clickedAgentDialogs = data.data as AgentDialog[]
                })
            }
            {
                const res = await fetchCustom(`/api/experiments/${this.expID}/agents/${this.clickedAgentID}/survey`)
                const data = await res.json()
                runInAction(() => {
                    this._clickedAgentSurveys = data.data as AgentSurvey[]
                })
            }
        } catch (err) {
            message.error(`Failed to fetch agent: ${JSON.stringify(err)}`, 3);
            console.error('Failed to fetch agent: ', err);
        }
    }

    async _fetchMetrics() {
        if (this.expID === undefined) {
            return
        }
        try {
            const res = await fetchCustom(`/api/experiments/${this.expID}/metrics`)
            const data = await res.json()
            runInAction(() => {
                // Handle null or undefined data.data and sanitize metrics
                if (data.data && typeof data.data === 'object') {
                    const sanitized = new Map<string, ApiMetric[]>()
                    Object.entries(data.data).forEach(([key, arr]) => {
                        if (Array.isArray(arr)) {
                            const metrics = (arr as ApiMetric[]).filter(
                                (m) =>
                                    Number.isFinite(m.value) &&
                                    Number.isFinite(m.step)
                            )
                            if (metrics.length > 0) {
                                sanitized.set(key, metrics)
                            }
                        }
                    })
                    this._metrics = sanitized
                } else {
                    this._metrics = new Map();
                }
            })
        } catch (err) {
            message.error(`Failed to fetch metrics: ${JSON.stringify(err)}`, 3);
            console.error('Failed to fetch metrics: ', err);
        }
    }

    get metrics() {
        return this._metrics
    }

    async init(expID?: string) {
        message.loading({
            key: "loading",
            content: `Loading experiment ${expID} ...`
        }, 0)
        this.mapCenterDone = false
        await this._fetchSurveys()
        this.expID = expID
        this.clickedAgentID = undefined
        this._clickedAgentStatuses = []
        if (expID === undefined) {
            this._timeline = []
            this._agent2Profile = new Map()
            this.agents = new Map()
            this._metrics = new Map()
        } else {
            await this._fetchExperiment()
            await this._fetchAgentProfile()
            await this._fetchMetrics()
            if (this.experiment?.status === 2) {
                // completed -> fetch the newest data
                await this._fetchAllAgentStatusAndPrompt(this._currentTime)
            } else if (this.experiment?.status === 1) {
                // running -> fetch the newest data
                await this._fetchAllAgentStatusAndPrompt()
            }
        }
        message.destroy("loading")
    }

    async fetchByTime(time: Time) {
        if (this.expID === undefined) {
            return
        }
        this._currentTime = time
        await this._fetchAllAgentStatusAndPrompt(time)
        await this._fetchClickedAgent()
        await this._fetchMetrics()
    }

    // 获取最新的experiment数据，刷新所有数据，如果有clickedAgentID，也刷新clickedAgentID的数据
    async refresh() {
        // 1. 刷新experiment数据
        await this._fetchExperiment()
        // 2. 刷新所有agent数据
        await this._fetchAllAgentStatusAndPrompt(this._currentTime)
        // 3. 刷新clickedAgent数据
        await this._fetchClickedAgent()
        // 4. 刷新metrics数据
        await this._fetchMetrics()
    }

    async setClickedAgentID(agentID?: number) {
        this.clickedAgentID = agentID
        this._clickedAgentStatuses = []
        if (agentID === undefined) {
            return
        }
        await this._fetchClickedAgent()
    }

    get currentTime() {
        return this._currentTime
    }

    get id2surveys() {
        return this._id2surveys
    }

    get clickedAgent() {
        if (this.clickedAgentID === undefined) {
            return undefined
        }
        const a = this.agents.get(this.clickedAgentID)
        if (a === undefined) {
            return undefined
        }
        return { ...a }
    }

    get clickedAgentStatuses() {
        if (this._currentTime === undefined) {
            return []
        }
        if (this._clickedAgentStatuses === undefined) {
            return []
        }
        // filter by current time
        return this._clickedAgentStatuses.slice().filter(s => (s.day < this._currentTime!.day || s.day === this._currentTime!.day && s.t <= this._currentTime!.t))
    }

    get clickedAgentDialogs() {
        if (this._currentTime === undefined) {
            return []
        }
        if (this._clickedAgentDialogs === undefined) {
            return []
        }
        // filter by current time
        return this._clickedAgentDialogs.slice().filter(d => d.day < this._currentTime!.day || d.day === this._currentTime!.day && d.t <= this._currentTime!.t)
    }

    get clickedAgentSurveys() {
        if (this._currentTime === undefined) {
            return []
        }
        if (this._clickedAgentSurveys === undefined) {
            return []
        }
        // filter by current time
        return this._clickedAgentSurveys.slice().filter(s => s.day < this._currentTime!.day || s.day === this._currentTime!.day && s.t <= this._currentTime!.t)
    }
}

export const store = new ReplayStore()
export const StoreContext = React.createContext(store)
