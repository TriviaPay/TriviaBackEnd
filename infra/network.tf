resource "oci_core_vcn" "main" {
  compartment_id = var.compartment_ocid
  display_name   = "${var.resource_prefix}-vcn"
  cidr_blocks    = [var.vcn_cidr]
  dns_label      = "triviapay"
}

resource "oci_core_internet_gateway" "igw" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${var.resource_prefix}-igw"
  enabled        = true
}

resource "oci_core_nat_gateway" "nat" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${var.resource_prefix}-nat"
}

data "oci_core_services" "all" {
  filter {
    name   = "name"
    values = ["All .* Services In Oracle Services Network"]
    regex  = true
  }
}

resource "oci_core_service_gateway" "sgw" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${var.resource_prefix}-sgw"

  services {
    service_id = data.oci_core_services.all.services[0].id
  }
}

resource "oci_core_route_table" "public" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${var.resource_prefix}-public-rt"

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.igw.id
  }
}

resource "oci_core_route_table" "private" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${var.resource_prefix}-private-rt"

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_nat_gateway.nat.id
  }

  route_rules {
    destination       = data.oci_core_services.all.services[0].cidr_block
    destination_type  = "SERVICE_CIDR_BLOCK"
    network_entity_id = oci_core_service_gateway.sgw.id
  }
}

resource "oci_core_subnet" "public" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.main.id
  display_name               = "${var.resource_prefix}-public"
  cidr_block                 = var.public_subnet_cidr
  route_table_id             = oci_core_route_table.public.id
  prohibit_public_ip_on_vnic = false
  dns_label                  = "public"
}

resource "oci_core_subnet" "private" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.main.id
  display_name               = "${var.resource_prefix}-private"
  cidr_block                 = var.private_subnet_cidr
  route_table_id             = oci_core_route_table.private.id
  prohibit_public_ip_on_vnic = true
  dns_label                  = "private"
}

resource "oci_core_network_security_group" "lb" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${var.resource_prefix}-lb-nsg"
}

resource "oci_core_network_security_group" "api" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${var.resource_prefix}-api-nsg"
}

resource "oci_core_network_security_group_security_rule" "lb_ingress" {
  for_each                 = toset(var.lb_ingress_cidrs)
  network_security_group_id = oci_core_network_security_group.lb.id
  direction                = "INGRESS"
  protocol                 = "6"
  source                   = each.value
  source_type              = "CIDR_BLOCK"

  tcp_options {
    destination_port_range {
      min = var.lb_listener_port
      max = var.lb_listener_port
    }
  }
}

resource "oci_core_network_security_group_security_rule" "lb_egress" {
  network_security_group_id = oci_core_network_security_group.lb.id
  direction                = "EGRESS"
  protocol                 = "6"
  destination              = "0.0.0.0/0"
  destination_type         = "CIDR_BLOCK"

  tcp_options {
    destination_port_range {
      min = var.backend_port
      max = var.backend_port
    }
  }
}

resource "oci_core_network_security_group_security_rule" "api_ingress" {
  network_security_group_id = oci_core_network_security_group.api.id
  direction                = "INGRESS"
  protocol                 = "6"
  source_type              = "NETWORK_SECURITY_GROUP"
  source                   = oci_core_network_security_group.lb.id

  tcp_options {
    destination_port_range {
      min = var.backend_port
      max = var.backend_port
    }
  }
}

resource "oci_core_network_security_group_security_rule" "api_egress" {
  network_security_group_id = oci_core_network_security_group.api.id
  direction                = "EGRESS"
  protocol                 = "all"
  destination              = "0.0.0.0/0"
  destination_type         = "CIDR_BLOCK"
}
