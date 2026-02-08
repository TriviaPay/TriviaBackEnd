resource "oci_kms_vault" "main" {
  compartment_id = var.compartment_ocid
  display_name   = "${var.resource_prefix}-vault"
  vault_type     = "DEFAULT"
}

resource "oci_kms_key" "secrets" {
  compartment_id      = var.compartment_ocid
  display_name        = "${var.resource_prefix}-secrets-key"
  management_endpoint = oci_kms_vault.main.management_endpoint

  key_shape {
    algorithm = "AES"
    length    = 32
  }
}

resource "oci_vault_secret" "app_env" {
  compartment_id = var.compartment_ocid
  vault_id       = oci_kms_vault.main.id
  key_id         = oci_kms_key.secrets.id
  secret_name    = "${var.resource_prefix}-app-env"
  description    = "App environment file contents"

  secret_content {
    content_type = "BASE64"
    content      = base64encode("CHANGEME")
  }
}

resource "oci_vault_secret" "ocir_username" {
  compartment_id = var.compartment_ocid
  vault_id       = oci_kms_vault.main.id
  key_id         = oci_kms_key.secrets.id
  secret_name    = "${var.resource_prefix}-ocir-username"
  description    = "OCIR username (tenancy/username)"

  secret_content {
    content_type = "BASE64"
    content      = base64encode("CHANGEME")
  }
}

resource "oci_vault_secret" "ocir_auth_token" {
  compartment_id = var.compartment_ocid
  vault_id       = oci_kms_vault.main.id
  key_id         = oci_kms_key.secrets.id
  secret_name    = "${var.resource_prefix}-ocir-auth-token"
  description    = "OCIR auth token"

  secret_content {
    content_type = "BASE64"
    content      = base64encode("CHANGEME")
  }
}
