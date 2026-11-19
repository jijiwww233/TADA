import type { Source } from '../search/contract'

export interface Project { id: number; name: string; brief: string; created_at: string }
export interface Pin {
  id: number; project_id: number; image_id: number; purposes: string[]; note: string
  created_at: string; needs_purpose: boolean
}
export interface ProjectImage {
  image_id: number; url: string; width: number | null; height: number | null
  original_name: string | null; created_at: string; sources: Source[]; is_video: boolean
}
export interface ProjectList { items: Project[]; allowed_purposes: string[] }
export interface PinsResponse { items: Pin[]; images: ProjectImage[]; allowed_purposes: string[] }
export interface ProjectDetail extends Project {
  pins: Pin[]; image_ids: number[]; images: ProjectImage[]; allowed_purposes: string[]
}
export interface ProjectApi {
  listProjects(): Promise<ProjectList>
  createProject(input: { name: string; brief: string }): Promise<Project>
  getProject(projectId: number): Promise<ProjectDetail>
  updateProject(projectId: number, input: { brief: string }): Promise<Project>
  listPins(projectId: number): Promise<PinsResponse>
  pinImage(projectId: number, imageId: number, purposes: string[], note?: string | null): Promise<Pin>
  removePurpose(projectId: number, imageId: number, purpose: string): Promise<Pin | null>
}
