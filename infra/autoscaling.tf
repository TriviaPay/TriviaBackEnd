resource "oci_autoscaling_auto_scaling_configuration" "pool" {
  compartment_id       = var.compartment_ocid
  display_name         = "${var.resource_prefix}-autoscale"
  cool_down_in_seconds = var.autoscaling_cooldown_seconds
  is_enabled           = true

  resource {
    id   = oci_core_instance_pool.api.id
    type = "instancePool"
  }

  capacity {
    min     = 1
    max     = var.instance_pool_max
    initial = var.instance_pool_size
  }

  policies {
    display_name = "scale-out"
    policy_type  = "threshold"

    rules {
      action {
        type  = "CHANGE_COUNT_BY"
        value = var.autoscaling_scale_out_step
      }

      metric {
        metric_type         = "CPU_UTILIZATION"
        threshold           = var.autoscaling_scale_out_cpu
        operator            = "GT"
        duration_in_minutes = 5
      }
    }
  }

  policies {
    display_name = "scale-in"
    policy_type  = "threshold"

    rules {
      action {
        type  = "CHANGE_COUNT_BY"
        value = var.autoscaling_scale_in_step
      }

      metric {
        metric_type         = "CPU_UTILIZATION"
        threshold           = var.autoscaling_scale_in_cpu
        operator            = "LT"
        duration_in_minutes = 10
      }
    }
  }
}
