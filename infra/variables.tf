variable "tenancy_ocid" {
  type = string
}

variable "compartment_ocid" {
  type = string
}

variable "compartment_name" {
  type = string
}

variable "region" {
  type = string
}

variable "availability_domain" {
  type    = string
  default = ""
}

variable "resource_prefix" {
  type    = string
  default = "triviapay"
}

variable "vcn_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "public_subnet_cidr" {
  type    = string
  default = "10.0.0.0/24"
}

variable "private_subnet_cidr" {
  type    = string
  default = "10.0.1.0/24"
}

variable "lb_ingress_cidrs" {
  type    = list(string)
  default = ["0.0.0.0/0"]
}

variable "lb_listener_port" {
  type    = number
  default = 80
}

variable "backend_port" {
  type    = number
  default = 8000
}

variable "lb_min_bandwidth_mbps" {
  type    = number
  default = 10
}

variable "lb_max_bandwidth_mbps" {
  type    = number
  default = 100
}

variable "instance_shape" {
  type    = string
  default = "VM.Standard.A1.Flex"
}

variable "instance_ocpus" {
  type    = number
  default = 2
}

variable "instance_memory_gbs" {
  type    = number
  default = 12
}

variable "instance_image_id" {
  type = string
}

variable "instance_pool_size" {
  type    = number
  default = 1
}

variable "instance_pool_max" {
  type    = number
  default = 4
}

variable "autoscaling_cooldown_seconds" {
  type    = number
  default = 300
}

variable "autoscaling_scale_out_cpu" {
  type    = number
  default = 70
}

variable "autoscaling_scale_in_cpu" {
  type    = number
  default = 30
}

variable "autoscaling_scale_out_step" {
  type    = number
  default = 1
}

variable "autoscaling_scale_in_step" {
  type    = number
  default = -1
}

variable "ocir_namespace" {
  type = string
}

variable "ocir_registry" {
  type    = string
  default = ""
}

variable "ocir_repo_name" {
  type = string
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "log_source_paths" {
  type    = list(string)
  default = ["/var/log/triviapay/app.log"]
}
