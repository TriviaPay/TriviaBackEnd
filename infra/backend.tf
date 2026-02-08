terraform {
  backend "oci" {
    bucket           = "REPLACE_ME"
    namespace        = "REPLACE_ME"
    region           = "REPLACE_ME"
    compartment_ocid = "REPLACE_ME"
    key              = "triviapay/terraform.tfstate"
  }
}
