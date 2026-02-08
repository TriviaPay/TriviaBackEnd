resource "oci_core_instance_configuration" "api" {
  compartment_id = var.compartment_ocid
  display_name   = "${var.resource_prefix}-api-${var.image_tag}"

  instance_details {
    instance_type = "compute"

    launch_details {
      compartment_id = var.compartment_ocid
      display_name   = "${var.resource_prefix}-api"
      shape          = var.instance_shape

      shape_config {
        ocpus         = var.instance_ocpus
        memory_in_gbs = var.instance_memory_gbs
      }

      create_vnic_details {
        subnet_id        = oci_core_subnet.private.id
        assign_public_ip = false
        nsg_ids          = [oci_core_network_security_group.api.id]
      }

      metadata = {
        user_data = local.cloud_init_b64
      }

      agent_config {
        is_management_disabled = false
        is_monitoring_disabled = false

        plugins_config {
          name          = "CustomLogsMonitoring"
          desired_state = "ENABLED"
        }
      }

      source_details {
        source_type = "image"
        image_id    = var.instance_image_id
      }
    }
  }
}

resource "oci_core_instance_pool" "api" {
  compartment_id            = var.compartment_ocid
  display_name              = "${var.resource_prefix}-pool"
  instance_configuration_id = oci_core_instance_configuration.api.id
  size                      = var.instance_pool_size

  placement_configurations {
    availability_domain = local.availability_domain
    primary_subnet_id   = oci_core_subnet.private.id
  }

  load_balancers {
    load_balancer_id = oci_load_balancer_load_balancer.main.id
    backend_set_name = oci_load_balancer_backend_set.api.name
    port             = var.backend_port
    vnic_selection   = "PrimaryVnic"
  }
}
