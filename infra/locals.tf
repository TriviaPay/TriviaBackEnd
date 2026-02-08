data "oci_identity_availability_domains" "ads" {
  compartment_id = var.tenancy_ocid
}

locals {
  availability_domain = var.availability_domain != "" ? var.availability_domain : data.oci_identity_availability_domains.ads.availability_domains[0].name
  ocir_registry       = var.ocir_registry != "" ? var.ocir_registry : "${var.region}.ocir.io"
  image               = "${local.ocir_registry}/${var.ocir_namespace}/${var.ocir_repo_name}:${var.image_tag}"

  cloud_init = templatefile("${path.module}/templates/cloud-init.sh.tmpl", {
    ocir_registry              = local.ocir_registry
    ocir_username_secret_id    = oci_vault_secret.ocir_username.id
    ocir_auth_token_secret_id  = oci_vault_secret.ocir_auth_token.id
    app_env_secret_id          = oci_vault_secret.app_env.id
    image                      = local.image
    backend_port               = var.backend_port
  })

  cloud_init_b64 = base64encode(local.cloud_init)
}
