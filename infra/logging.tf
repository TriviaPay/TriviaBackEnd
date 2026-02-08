resource "oci_logging_log_group" "main" {
  compartment_id = var.compartment_ocid
  display_name   = "${var.resource_prefix}-logs"
}

resource "oci_logging_log" "lb_access" {
  display_name = "${var.resource_prefix}-lb-access"
  log_group_id = oci_logging_log_group.main.id
  log_type     = "SERVICE"
  is_enabled   = true

  configuration {
    source {
      category = "access"
      resource = oci_load_balancer_load_balancer.main.id
      service  = "loadbalancer"
    }
  }
}

resource "oci_logging_log" "app" {
  display_name = "${var.resource_prefix}-app"
  log_group_id = oci_logging_log_group.main.id
  log_type     = "CUSTOM"
  is_enabled   = true

  configuration {
    source {
      category = "custom"
      resource = var.resource_prefix
      service  = "custom"
    }
  }
}

resource "oci_logging_unified_agent_configuration" "app" {
  compartment_id = var.compartment_ocid
  display_name   = "${var.resource_prefix}-uma"
  description    = "Collect app/container logs"
  is_enabled     = true

  service_configuration {
    configuration_type = "LOGGING"

    destination {
      log_object_id = oci_logging_log.app.id
    }

    sources {
      source_type = "LOG_TAIL"
      name        = "app-log"
      paths       = var.log_source_paths

      parser {
        parser_type = "NONE"
      }
    }
  }

  group_association {
    group_list = [oci_identity_dynamic_group.instance_pool.id]
    group_type = "DYNAMIC_GROUP"
  }
}
