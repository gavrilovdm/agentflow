variable "region" {
  type    = string
  default = "eu-central-1"
}
variable "env" {
  type    = string
  default = "prod"
}
variable "github_repo" {
  description = "owner/name of this repo — allowed to push images via GitHub OIDC"
  type        = string
}
variable "db_instance_class" {
  type    = string
  default = "db.t4g.medium"
}
